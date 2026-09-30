"""Pull a product's photo, name, and size from its web page, and cut the
product out of its photo background."""

import io
import json
import re
from collections import deque
from urllib.parse import urljoin

import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFilter

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Sizes outside these ranges (inches) are treated as misreads and ignored.
SANE_RANGE = {
    "vanity":        {"HEIGHT": (18, 50), "WIDTH": (12, 120), "DEPTH": (12, 26)},
    "faucet":        {"HEIGHT": (2, 25), "WIDTH": (1, 20), "DEPTH": (2, 14)},
    "mirror":        {"HEIGHT": (12, 72), "WIDTH": (10, 96), "DEPTH": (0.25, 8)},
    "sconce":        {"HEIGHT": (4, 36), "WIDTH": (2, 16), "DEPTH": (2, 14)},
    "vanity_light":  {"HEIGHT": (3, 20), "WIDTH": (8, 72), "DEPTH": (2, 14)},
    "toilet":        {"HEIGHT": (14, 36), "WIDTH": (12, 24), "DEPTH": (18, 32)},
    "tub":           {"HEIGHT": (12, 30), "WIDTH": (48, 80), "DEPTH": (26, 45)},
    "shower":        {"HEIGHT": (60, 96), "WIDTH": (30, 96), "DEPTH": (30, 72)},
    "shower_trim":   {"HEIGHT": (4, 80), "WIDTH": (2, 30), "DEPTH": (1, 24)},
    "ceiling_light": {"HEIGHT": (2, 40), "WIDTH": (6, 40), "DEPTH": (6, 40)},
    "door":          {"HEIGHT": (72, 96), "WIDTH": (18, 42), "DEPTH": (1, 3)},
    "window":        {"HEIGHT": (12, 72), "WIDTH": (12, 96), "DEPTH": (1, 8)},
}


# ---------------------------------------------------------------- numbers

UNICODE_FRACTIONS = {"½": " 1/2", "¼": " 1/4", "¾": " 3/4", "⅛": " 1/8",
                     "⅜": " 3/8", "⅝": " 5/8", "⅞": " 7/8", "⅓": " 1/3", "⅔": " 2/3"}


def normalize(text):
    for sym, frac in UNICODE_FRACTIONS.items():
        text = text.replace(sym, frac)
    return text.replace("″", '"').replace("”", '"').replace(" ", " ")


def to_inches(value):
    """Parse '34.5', '34 1/2', '34-1/2 in', '34½' into a float, or None."""
    if value is None or value == "":
        return None
    m = re.search(r"(\d+(?:\.\d+)?)(?:[\s-]+(\d+)/(\d+))?", normalize(str(value)))
    if not m:
        return None
    number = float(m.group(1))
    if m.group(2) and float(m.group(3)):
        number += float(m.group(2)) / float(m.group(3))
    return number if 0 <= number <= 1000 else None


def fmt_in(x):
    """34.5 -> 34-1/2\" (nearest 1/8)."""
    eighths = round(x * 8)
    whole, rest = divmod(eighths, 8)
    if not rest:
        return f'{whole}"'
    den = 8
    while rest % 2 == 0:
        rest, den = rest // 2, den // 2
    return f'{whole}-{rest}/{den}"' if whole else f'{rest}/{den}"'


def fmt_dim(x):
    """Architectural style: 96 -> 8'-0\", 34.5 -> 34-1/2\", 62.5 -> 5'-2 1/2\"."""
    if x < 24:
        return fmt_in(x)
    feet, inches = divmod(round(x * 8) / 8, 12)
    return f"{int(feet)}'-{fmt_in(inches).replace('-', ' ')}"


# ---------------------------------------------------------------- page parsing

def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def finish_score(finish, *texts):
    """Share of the finish's words that appear in the given texts (0..1)."""
    want = words(finish)
    if not want:
        return 0.0
    have = set().union(*(words(t) for t in texts))
    return len(want & have) / len(want)


def json_ld_products(soup):
    found = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" in kinds or "ProductGroup" in kinds:
                found.append(node)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return found


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def image_urls(value):
    urls = []
    for v in as_list(value):
        if isinstance(v, str):
            urls.append(v)
        elif isinstance(v, dict) and (v.get("url") or v.get("contentUrl")):
            urls.append(v.get("url") or v.get("contentUrl"))
    return urls


def shopify_candidates(page_url):
    """Shopify stores expose every variant (finish) with its own photo."""
    base = page_url.split("?")[0].rstrip("/")
    if "/products/" not in base:
        return []
    try:
        data = fetch(base + ".js").json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for variant in data.get("variants", []):
        img = (variant.get("featured_image") or {}).get("src")
        if img:
            out.append((img, " ".join([variant.get("title") or ""] + (variant.get("options") or []))))
    for img in data.get("images", []):
        out.append((img, data.get("title", "")))
    return out


def collect_candidates(soup, page_url):
    """Return (image_url, describing_text) pairs, best guesses first."""
    cands = []
    for product in json_ld_products(soup):
        variants = as_list(product.get("hasVariant")) + as_list(product.get("model"))
        for v in variants:
            if isinstance(v, dict):
                text = " ".join(str(v.get(k, "")) for k in ("name", "color", "sku", "description"))
                cands += [(u, text) for u in image_urls(v.get("image"))]
        text = " ".join(str(product.get(k, "")) for k in ("name", "color"))
        cands += [(u, text) for u in image_urls(product.get("image"))]

    cands += shopify_candidates(page_url)

    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            cands.append((tag["content"], ""))

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-zoom-image") or img.get("src")
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[-1].split()[0]
        if src and not src.startswith("data:"):
            cands.append((src, " ".join([img.get("alt", ""), img.get("title", ""), src])))

    seen, unique = set(), []
    for url, text in cands:
        url = urljoin(page_url, url.strip())
        if url.startswith("//"):
            url = "https:" + url
        if url not in seen and not re.search(r"logo|icon|sprite|badge|\.svg", url, re.I):
            seen.add(url)
            unique.append((url, text))
    return unique


def product_name(soup):
    for product in json_ld_products(soup):
        if product.get("name"):
            return str(product["name"]).strip()
    tag = soup.find("meta", attrs={"property": "og:title"})
    if tag and tag.get("content"):
        return tag["content"].split(" | ")[0].strip()
    if soup.title and soup.title.string:
        return soup.title.string.split(" | ")[0].strip()
    return ""


# Label words that point to the whole product's size vs. a part of it.
GOOD_WORDS = {"overall": 3, "total": 3, "product": 2, "assembled": 2, "item": 1,
              "with top": 2, "with countertop": 2, "including": 1}
BAD_WORDS = ["spout", "handle", "rough", "backsplash", "toe", "kick", "drawer", "door",
             "package", "packaged", "shipping", "carton", "box", "sink", "bowl", "basin",
             "drain", "opening", "hole", "deck", "clearance", "interior", "inside", "seat",
             "lever", "reach", "center", "spread", "shelf", "leg", "bevel", "frame depth",
             "water", "soaking", "rim", "canopy", "shade", "backplate", "chain", "glass"]
DIM_WORDS = {"height": "HEIGHT", "h": "HEIGHT", "width": "WIDTH", "w": "WIDTH",
             "depth": "DEPTH", "d": "DEPTH", "length": "WIDTH", "l": "WIDTH"}
UNIT = r"(?P<unit>inches|inch|in\b\.?|\"|''|cm\b|mm\b)"
NUM = r"(?P<num>\d+(?:\.\d+)?(?:[\s-]+\d+/\d+)?)"
LABEL_FIRST = re.compile(
    r"\b(?P<dim>height|width|depth|length|h|w|d|l)\b"
    r"(?P<mid>(?:\s+(?:with|w/|incl\.?|including|without|top|countertop|counter|and|sink|mirror|frame))*)"
    r"\s*(?:\(\s*(?P<unit1>in|inches|cm|mm)\.?\s*\))?\s*[:=\-]?\s*" + NUM + r"\s*" + UNIT + "?"
)
# "36 in. W x 22 in. D x 34.5 in. H" (single letters only, so "19 in. Depth 28" isn't read as depth 19)
NUMBER_FIRST = re.compile(NUM + r"\s*" + UNIT + r"\s*\(?\s*(?P<dim>h|w|d|l)\b")


def label_before(text, pos):
    """The words right before pos, stopping at the previous value or separator,
    so 'Spout Reach: 5 in. Overall Height' reads as just 'overall'."""
    return re.split(r"[\d.:;|,()\"]", text[max(0, pos - 40):pos])[-1]


def score_label(context):
    score = sum(v for k, v in GOOD_WORDS.items() if k in context)
    score -= 5 * sum(1 for k in BAD_WORDS if k in context)
    if "cabinet" in context:
        score -= 1
    return score


def to_unit_inches(num, unit):
    value = to_inches(num)
    if value is None or not unit:
        return None
    unit = unit.strip(". ")
    if unit == "cm":
        return value / 2.54
    if unit == "mm":
        return value / 25.4
    return value


def parse_value(text):
    m = re.search(NUM + r"\s*" + UNIT + "?", normalize(text.lower()))
    return to_unit_inches(m.group("num"), m.group("unit") or "in") if m else None


def find_dimensions(soup, kind):
    """Best guess of the product's overall height/width/depth in inches."""
    ranges = SANE_RANGE.get(kind, {})
    found = {"HEIGHT": [], "WIDTH": [], "DEPTH": []}

    def add(key, value, score):
        low, high = ranges.get(key, (0.1, 500))
        if value is not None and low <= value <= high:
            found[key].append((score, value))

    spec_text = []
    for product in json_ld_products(soup):
        for key in found:
            val = product.get(key.lower())
            if isinstance(val, dict):
                unit = {"CMT": "cm", "MMT": "mm"}.get(val.get("unitCode"), val.get("unitText") or "in")
                val = f'{val.get("value", "")} {unit}'
            if val:
                add(key, parse_value(str(val)), 4)
        for prop in as_list(product.get("additionalProperty")):
            if isinstance(prop, dict):
                spec_text.append(f'{prop.get("name", "")}: {prop.get("value", "")} {prop.get("unitText", "")}')

    for text, bonus in ((" . ".join(spec_text), 1), (soup.get_text(" ", strip=True), 0)):
        text = normalize(text.lower())
        for m in LABEL_FIRST.finditer(text):
            key = DIM_WORDS[m.group("dim")]
            unit = m.group("unit1") or m.group("unit")
            context = label_before(text, m.start("dim")) + m.group("dim") + m.group("mid")
            add(key, to_unit_inches(m.group("num"), unit), score_label(context) + bonus)
        for m in NUMBER_FIRST.finditer(text):
            key = DIM_WORDS[m.group("dim")]
            context = label_before(text, m.start())
            add(key, to_unit_inches(m.group("num"), m.group("unit")), score_label(context) + bonus + 1)

    dims = {}
    for key, options in found.items():
        if options:
            best = max(s for s, _ in options)
            if best > -5:                       # every match was a part, not the product
                dims[key] = next(v for s, v in options if s == best)
    return dims


# ---------------------------------------------------------------- photos

def download_image(url):
    img = Image.open(io.BytesIO(fetch(url).content))
    img.load()
    return img


def has_transparency(img):
    if img.mode not in ("RGBA", "LA", "P"):
        return False
    alpha = np.asarray(img.convert("RGBA"))[:, :, 3]
    return (alpha < 250).mean() > 0.05


def studio_score(img):
    """How plain and light the photo's border is (1.0 = clean studio shot)."""
    if has_transparency(img):
        return 1.0
    small = img.convert("RGB").resize((200, max(1, round(200 * img.height / img.width))))
    rgb = np.asarray(small, dtype=int)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    median = np.median(border, axis=0)
    plain = (np.abs(border - median).max(axis=1) < 20).mean()
    return plain if median.min() > 200 else plain * 0.5


def get_product(label, url, finish, kind, prefer_studio=True):
    """Returns (photo, dimensions dict, product name)."""
    resp = fetch(url)
    if resp.headers.get("Content-Type", "").startswith("image/"):
        print(f"  {label}: direct image link")
        img = Image.open(io.BytesIO(resp.content))
        img.load()
        return img, {}, ""

    soup = BeautifulSoup(resp.content, "html.parser")   # detects encoding (keeps ½ etc.)
    cands = collect_candidates(soup, resp.url)
    if not cands:
        raise RuntimeError("no product photo found on the page")
    scored = [(finish_score(finish, text, u), i, u) for i, (u, text) in enumerate(cands)]
    scored.sort(key=lambda s: (-s[0], s[1]))
    if finish and scored[0][0] == 0:
        print(f"  {label}: WARNING - no photo labeled '{finish}', using the main photo")

    # Among the photos that best match the finish, prefer a plain studio shot
    # over a room/lifestyle shot, which is much harder to cut out.
    best, best_key = None, None
    for fscore, order, img_url in scored[:8]:
        if best is not None and fscore < best_key[0]:
            break
        try:
            img = download_image(img_url)
        except Exception:
            continue
        if min(img.size) < 200:            # skip thumbnails
            continue
        studio = studio_score(img) if prefer_studio else 1.0
        key = (fscore, studio >= 0.85)
        if best is None or key > best_key:
            best, best_key = (img, img_url, studio), key
        if studio >= 0.85:
            break
    if best is None:
        raise RuntimeError("could not download a usable product photo")
    img, img_url, studio = best
    print(f"  {label}: photo {img_url}")
    if studio < 0.85:
        print(f"  {label}: WARNING - photo has a busy background; the cut-out may be rough. "
              "Paste a plain-background photo's image address as the URL for a cleaner result.")
    return img, find_dimensions(soup, kind), product_name(soup)


# ---------------------------------------------------------------- background removal

def remove_background(img):
    img = img.convert("RGBA")
    if has_transparency(img):
        return trim(img)
    try:
        from rembg import remove           # best quality, optional
        cut = remove(img)
        cut.putalpha(drop_specks(cut.getchannel("A")))
        return trim(cut)
    except ImportError:
        pass

    # Flood-fill the plain background (and light drop shadows) in from the
    # edges. If that eats nearly everything - e.g. a white product on white -
    # retry stricter. Pillow's thresh is summed over R+G+B (45 = ~15/channel).
    mask = None
    for thresh, shadows in ((45, True), (45, False), (28, False), (14, False)):
        mask = flood_mask(img, thresh, shadows)
        kept = (np.asarray(mask) > 128).mean()
        if 0.03 < kept < 0.97:
            break
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))  # drop dust
    mask = drop_specks(mask)
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))  # clean halo
    img.putalpha(mask)
    return trim(img)


def flood_mask(img, thresh, shadows=False):
    rgb = img.convert("RGB")
    if shadows:
        # Light, colorless gray touching the background = drop shadow.
        arr = np.asarray(rgb).copy()
        gray = (arr.max(axis=2).astype(int) - arr.min(axis=2)) < 12
        arr[gray & (arr.min(axis=2) > 225)] = 255
        rgb = Image.fromarray(arr)
    filled = rgb.copy()
    w, h = rgb.size
    marker = (1, 254, 3)
    step = max(1, min(w, h) // 40)
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
    seeds += [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    for seed in seeds:
        if filled.getpixel(seed) != marker:
            ImageDraw.floodfill(filled, seed, marker, thresh=thresh)
    bg = np.all(np.asarray(filled) == marker, axis=2) & ~np.all(np.asarray(rgb) == marker, axis=2)
    return Image.fromarray(np.where(bg, 0, 255).astype(np.uint8))


def drop_specks(mask, keep_ratio=0.03):
    """Remove small separate blobs (watermarks, dimension text, dust) so they
    don't throw off the crop and scale. Keeps every piece at least 3% the size
    of the biggest one, so separate faucet handles survive."""
    w, h = mask.size
    sw = 200
    sh = max(1, round(h * sw / w))
    small = np.asarray(mask.resize((sw, sh), Image.NEAREST)) > 128
    labels = np.zeros(small.shape, dtype=np.int32)
    areas = [0]
    for y0, x0 in zip(*np.nonzero(small)):
        if labels[y0, x0]:
            continue
        label = len(areas)
        labels[y0, x0] = label
        queue, area = deque([(y0, x0)]), 0
        while queue:
            y, x = queue.popleft()
            area += 1
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = label
                    queue.append((ny, nx))
        areas.append(area)
    if len(areas) <= 2:
        return mask
    keep = np.array(areas) >= keep_ratio * max(areas)
    keep[0] = False
    keep_small = Image.fromarray((keep[labels] * 255).astype(np.uint8))
    keep_full = keep_small.resize((w, h), Image.NEAREST).filter(ImageFilter.MaxFilter(5))
    return Image.fromarray(np.minimum(np.asarray(mask), np.asarray(keep_full)))


def trim(img):
    box = img.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox()
    return img.crop(box) if box else img


def dominant_color(img):
    """Median color of the visible pixels."""
    arr = np.asarray(img.convert("RGBA").resize((80, max(1, round(80 * img.height / img.width)))))
    px = arr[arr[:, :, 3] > 128][:, :3] if arr.shape[2] == 4 else arr.reshape(-1, 3)
    if not len(px):
        return (200, 200, 200)
    return tuple(int(c) for c in np.median(px, axis=0))


def real_size(label, kind, entry, scraped, img, default):
    """(width, height, depth) in inches: project file > website > photo proportions > default.
    The photo is never stretched more than 15%; beyond that it's probably an
    angled shot, so height wins and width follows the photo."""
    h = to_inches(entry.get("HEIGHT")) or scraped.get("HEIGHT")
    w = to_inches(entry.get("WIDTH")) or scraped.get("WIDTH")
    d = to_inches(entry.get("DEPTH")) or scraped.get("DEPTH") or default[2]
    if img is None:
        return w or default[0], h or default[1], d
    aspect = img.width / img.height
    if h and w:
        stretch = (w / h) / aspect
        if not 0.85 <= stretch <= 1.15:
            print(f"  {label}: photo proportions don't match {fmt_in(w)} x {fmt_in(h)} "
                  "(angled shot?). Photo sized by height; plan uses the listed width.")
    elif h:
        w = h * aspect
    elif w:
        h = w / aspect
    else:
        h = default[1]
        w = h * aspect
        print(f"  {label}: WARNING - no size found, guessing {fmt_in(h)} tall. Set HEIGHT in the project file.")
    return w, h, d

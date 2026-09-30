"""Bathroom mockup builder.

Fill in products.txt (vanity, faucet, mirror links + finish), then run:

    python mockup.py

The script pulls each product photo from its page, removes the background,
scales every item to its real size in inches, and draws them on a wall:
vanity on the floor, faucet on the counter, mirror above. Labels show each
product's name, finish, and size, and a ruler marks the key heights.
"""

import io
import json
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "products.txt"
OUTPUT_FILE = HERE / "mockup.png"

ITEMS = ["VANITY", "FAUCET", "MIRROR"]
DEFAULT_SETTINGS = {
    "WALL_COLOR": "#EEEBE6",
    "FLOOR_COLOR": "#BCA98F",
    "MIRROR_GAP": "6",
    "LABELS": "yes",
    "PIXELS_PER_INCH": "20",
}
MIRROR_MIN_BOTTOM_IN = 40     # mirror bottom never lower than this off the floor
MARGIN_IN = 4                 # space around the items, inches
FLOOR_BAND_IN = 4             # visible floor strip under the floor line
RULER_COL_IN = 10             # left column for height marks
LABEL_COL_IN = 34             # right column for product labels

# Used only when neither products.txt nor the website gives a size.
DEFAULT_HEIGHT_IN = {"VANITY": 34.5, "FAUCET": 7.0, "MIRROR": 30.0}
# Sizes outside these ranges (inches) are treated as misreads and ignored.
SANE_RANGE = {
    "VANITY": {"HEIGHT": (18, 50), "WIDTH": (12, 96)},
    "FAUCET": {"HEIGHT": (2, 25), "WIDTH": (1, 20)},
    "MIRROR": {"HEIGHT": (12, 72), "WIDTH": (10, 96)},
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------- config

def read_config(path):
    items, section = {}, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\w+)\]", line)
        if m:
            section = m.group(1).upper()
            items[section] = {}
        elif section and "=" in line:
            key, value = line.split("=", 1)
            items[section][key.strip().upper()] = value.strip()
    return items


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
    return number if 0 <= number <= 150 else None


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
              "with top": 2, "with countertop": 2, "including": 1, "faucet": 1, "mirror": 1}
BAD_WORDS = ["spout", "handle", "rough", "backsplash", "toe", "kick", "drawer", "door",
             "package", "packaged", "shipping", "carton", "box", "sink", "bowl", "basin",
             "drain", "opening", "hole", "deck", "clearance", "interior", "inside", "seat",
             "lever", "reach", "center", "spread", "shelf", "leg", "bevel", "frame depth"]
UNIT = r"(?P<unit>inches|inch|in\b\.?|\"|''|cm\b|mm\b)"
NUM = r"(?P<num>\d+(?:\.\d+)?(?:[\s-]+\d+/\d+)?)"
LABEL_FIRST = re.compile(
    r"\b(?P<dim>height|width|h|w)\b(?P<mid>(?:\s+(?:with|w/|incl\.?|including|without|top|countertop|counter|and|sink|mirror|frame))*)"
    r"\s*(?:\(\s*(?P<unit1>in|inches|cm|mm)\.?\s*\))?\s*[:=\-]?\s*"
    + NUM + r"\s*" + UNIT + "?"
)
NUMBER_FIRST = re.compile(NUM + r"\s*" + UNIT + r"\s*\(?\s*(?P<dim>height|width|h|w)\b")


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


def find_dimensions(soup, item):
    """Best guess of the product's overall height/width in inches."""
    ranges = SANE_RANGE[item]
    found = {"HEIGHT": [], "WIDTH": []}

    def add(key, value, score):
        low, high = ranges[key]
        if value is not None and low <= value <= high:
            found[key].append((score, value))

    spec_text = []
    for product in json_ld_products(soup):
        for key in ("HEIGHT", "WIDTH"):
            val = product.get(key.lower())
            if isinstance(val, dict):
                unit = {"CMT": "cm", "MMT": "mm"}.get(val.get("unitCode"), val.get("unitText") or "in")
                val = f'{val.get("value", "")} {unit}'
            if val:
                add(key, _parse_value(str(val)), 4)
        for prop in as_list(product.get("additionalProperty")):
            if isinstance(prop, dict):
                spec_text.append(f'{prop.get("name", "")}: {prop.get("value", "")} {prop.get("unitText", "")}')

    texts = [(" . ".join(spec_text), 1), (soup.get_text(" ", strip=True), 0)]
    for text, bonus in texts:
        text = normalize(text.lower())
        for m in LABEL_FIRST.finditer(text):
            key = "HEIGHT" if m.group("dim") in ("height", "h") else "WIDTH"
            unit = m.group("unit1") or m.group("unit")
            context = label_before(text, m.start("dim")) + m.group("dim") + m.group("mid")
            add(key, to_unit_inches(m.group("num"), unit), score_label(context) + bonus)
        for m in NUMBER_FIRST.finditer(text):
            key = "HEIGHT" if m.group("dim") in ("height", "h") else "WIDTH"
            context = label_before(text, m.start())
            add(key, to_unit_inches(m.group("num"), m.group("unit")), score_label(context) + bonus + 1)

    dims = {}
    for key, options in found.items():
        if options:
            best = max(s for s, _ in options)
            if best > -5:                       # every match was a part, not the product
                dims[key] = next(v for s, v in options if s == best)
    return dims


def _parse_value(text):
    m = re.search(NUM + r"\s*" + UNIT + "?", normalize(text.lower()))
    return to_unit_inches(m.group("num"), m.group("unit") or "in") if m else None


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


def get_product(name, entry):
    url, finish = entry.get("URL", ""), entry.get("FINISH", "")
    resp = fetch(url)
    if resp.headers.get("Content-Type", "").startswith("image/"):
        print(f"  {name}: direct image link")
        img = Image.open(io.BytesIO(resp.content))
        img.load()
        return img, {}, ""

    soup = BeautifulSoup(resp.content, "html.parser")  # detects encoding (keeps ½ etc.)
    cands = collect_candidates(soup, resp.url)
    if not cands:
        raise RuntimeError("no product photo found on the page")
    scored = [(finish_score(finish, text, u), i, u) for i, (u, text) in enumerate(cands)]
    scored.sort(key=lambda s: (-s[0], s[1]))
    if finish and scored[0][0] == 0:
        print(f"  {name}: WARNING - no photo labeled '{finish}', using the main photo")

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
        studio = studio_score(img)
        key = (fscore, studio >= 0.85)
        if best is None or key > best_key:
            best, best_key = (img, img_url, studio), key
        if studio >= 0.85:
            break
    if best is None:
        raise RuntimeError("could not download a usable product photo")
    img, img_url, studio = best
    print(f"  {name}: photo {img_url}")
    if studio < 0.85:
        print(f"  {name}: WARNING - photo has a busy background; the cut-out may be rough. "
              "Paste a plain-background photo's image address as the URL for a cleaner result.")
    return img, find_dimensions(soup, name), product_name(soup)


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


def real_size(name, entry, scraped, img):
    """Width/height in inches: products.txt > website > photo proportions > default.
    The photo is never stretched more than 15%; beyond that it's probably an
    angled shot, so height wins and width follows the photo."""
    h = to_inches(entry.get("HEIGHT")) or scraped.get("HEIGHT")
    w = to_inches(entry.get("WIDTH")) or scraped.get("WIDTH")
    aspect = img.width / img.height
    if h and w:
        stretch = (w / h) / aspect
        if not 0.85 <= stretch <= 1.15:
            print(f"  {name}: photo proportions don't match {fmt_in(w)} x {fmt_in(h)} "
                  "(angled shot?). Sizing by height.")
            w = h * aspect
    elif h:
        w = h * aspect
    elif w:
        h = w / aspect
    else:
        h = DEFAULT_HEIGHT_IN[name]
        w = h * aspect
        print(f"  {name}: WARNING - no size found, guessing {fmt_in(h)} tall. Set HEIGHT in products.txt.")
    return w, h


# ---------------------------------------------------------------- drawing

def load_font(size):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "Helvetica.ttc",
                 "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # Pillow < 10.1
        return ImageFont.load_default()


def parse_color(value):
    if not value or value.lower() in ("none", "transparent", "no"):
        return None
    try:
        return ImageColor.getrgb(value)[:3] + (255,)
    except ValueError:
        print(f"  Unknown color '{value}', using default")
        return None


def plan_layout(parts, settings):
    """Bottom height (inches off floor) for each item."""
    gap = to_inches(settings["MIRROR_GAP"]) or 0
    layout = {}
    counter = DEFAULT_HEIGHT_IN["VANITY"]
    if "VANITY" in parts:
        p = parts["VANITY"]
        bottom = p["off_floor"] if p["off_floor"] is not None else 0.0
        layout["VANITY"] = bottom
        counter = bottom + p["h"]
    faucet_top = counter
    if "FAUCET" in parts:
        p = parts["FAUCET"]
        bottom = p["off_floor"] if p["off_floor"] is not None else counter
        layout["FAUCET"] = bottom
        faucet_top = bottom + p["h"]
    if "MIRROR" in parts:
        p = parts["MIRROR"]
        default = max(faucet_top + gap, MIRROR_MIN_BOTTOM_IN)
        layout["MIRROR"] = p["off_floor"] if p["off_floor"] is not None else default
    return layout


def draw_mockup(parts, settings):
    layout = plan_layout(parts, settings)
    ppi = int(to_inches(settings["PIXELS_PER_INCH"]) or 20)
    wall = parse_color(settings["WALL_COLOR"])
    floor_color = parse_color(settings["FLOOR_COLOR"]) or (188, 169, 143, 255)
    labels = settings["LABELS"].lower() in ("yes", "y", "true", "1", "on")
    ink = (70, 66, 60, 255)
    faint = (70, 66, 60, 90)

    items_w = max(p["w"] for p in parts.values()) + 2 * MARGIN_IN
    left = RULER_COL_IN if labels else 0
    right = LABEL_COL_IN if labels else 0
    top_in = max(layout[n] + parts[n]["h"] for n in parts)
    total_w = left + items_w + right
    total_h = top_in + MARGIN_IN + FLOOR_BAND_IN
    W, H = round(total_w * ppi), round(total_h * ppi)

    canvas = Image.new("RGBA", (W, H), wall or (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    floor_y = round((total_h - FLOOR_BAND_IN) * ppi)
    if wall:
        draw.rectangle([0, floor_y, W, H], fill=floor_color)
    draw.line([0, floor_y, W, floor_y], fill=ink, width=max(2, ppi // 8))

    def y_of(inches):
        return round(floor_y - inches * ppi)

    center_x = (left + items_w / 2) * ppi
    boxes = {}
    for name in ["MIRROR", "VANITY", "FAUCET"]:   # faucet drawn last, on top
        if name not in parts:
            continue
        p = parts[name]
        img = p["img"].resize((max(1, round(p["w"] * ppi)), max(1, round(p["h"] * ppi))), Image.LANCZOS)
        x = round(center_x - img.width / 2)
        y = y_of(layout[name] + p["h"])
        canvas.alpha_composite(img, (x, y))
        boxes[name] = (x, y, x + img.width, y + img.height)

    if labels:
        font = load_font(max(12, round(ppi * 0.95)))
        small = load_font(max(10, round(ppi * 0.8)))
        line_h = round(ppi * 1.3)
        draw_ruler(draw, parts, layout, boxes, left * ppi, y_of, font, ink, faint, ppi)
        draw_labels(draw, parts, boxes, (left + items_w) * ppi, H, font, small, line_h, ink, ppi)
    return canvas, layout


def draw_ruler(draw, parts, layout, boxes, col_right, y_of, font, ink, faint, ppi):
    """Height marks on the left: counter, faucet top, mirror bottom and top."""
    marks = []
    if "VANITY" in parts:
        marks.append((layout["VANITY"] + parts["VANITY"]["h"], "VANITY", "counter"))
        if layout["VANITY"] > 0:
            marks.append((layout["VANITY"], "VANITY", "vanity bottom"))
    if "FAUCET" in parts:
        marks.append((layout["FAUCET"] + parts["FAUCET"]["h"], "FAUCET", "faucet top"))
    if "MIRROR" in parts:
        marks.append((layout["MIRROR"], "MIRROR", "mirror bottom"))
        marks.append((layout["MIRROR"] + parts["MIRROR"]["h"], "MIRROR", "mirror top"))

    line_x = col_right - ppi * 1.5
    draw.line([line_x, y_of(0), line_x, y_of(max(m[0] for m in marks))], fill=ink, width=2)
    last_y = None
    for inches, item, what in sorted(marks):
        y = y_of(inches)
        draw.line([line_x - ppi * 0.6, y, line_x + ppi * 0.6, y], fill=ink, width=2)
        draw.line([line_x + ppi * 0.6, y, boxes[item][0], y], fill=faint, width=1)
        if last_y is not None and last_y - y < ppi * 2.2:   # too close to the previous mark
            continue
        draw.text((line_x - ppi * 0.9, y), fmt_in(inches), font=font, fill=ink, anchor="rb")
        draw.text((line_x - ppi * 0.9, y + 2), what, font=font, fill=(70, 66, 60, 160), anchor="rt")
        last_y = y


def draw_labels(draw, parts, boxes, col_left, canvas_h, font, small, line_h, ink, ppi):
    """Name, finish, and size to the right of each item, spread so they don't overlap."""
    blocks = []
    for name, p in parts.items():
        title = p["name"] or name.title()
        if len(title) > 36:
            title = title[:35].rstrip() + "…"
        lines = [(name, small), (title, font)]
        if p["finish"]:
            lines.append((p["finish"], font))
        lines.append((f'{fmt_in(p["w"])} W x {fmt_in(p["h"])} H', font))
        x0, y0, x1, y1 = boxes[name]
        blocks.append([(y0 + y1) / 2, name, lines])

    blocks.sort(key=lambda b: b[0])
    heights = [len(b[2]) * line_h for b in blocks]
    tops = [b[0] - hgt / 2 for b, hgt in zip(blocks, heights)]
    for i in range(1, len(tops)):          # push down to avoid overlap
        tops[i] = max(tops[i], tops[i - 1] + heights[i - 1] + ppi)
    overflow = tops[-1] + heights[-1] - (canvas_h - ppi)
    if overflow > 0:                       # then pull back up if off the bottom
        tops = [t - overflow for t in tops]
        for i in range(len(tops) - 2, -1, -1):
            tops[i] = min(tops[i], tops[i + 1] - heights[i] - ppi)

    text_x = col_left + ppi * 2
    for (mid, name, lines), top in zip(blocks, tops):
        x1 = boxes[name][2]
        draw.line([x1 + ppi * 0.4, mid, text_x - ppi * 0.6, top + line_h * 0.6], fill=ink, width=2)
        for i, (text, fnt) in enumerate(lines):
            color = (70, 66, 60, 150) if i == 0 else ink
            draw.text((text_x, top + i * line_h), text, font=fnt, fill=color)


# ---------------------------------------------------------------- main

def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else CONFIG_FILE
    config = read_config(config_path)
    settings = {**DEFAULT_SETTINGS, **{k: v for k, v in config.get("SETTINGS", {}).items() if v}}

    parts = {}
    for name in ITEMS:
        entry = config.get(name, {})
        if not entry.get("URL"):
            print(f"  {name}: no URL, skipped")
            continue
        try:
            raw, scraped, title = get_product(name, entry)
        except Exception as exc:
            host = urlparse(entry["URL"]).netloc
            print(f"  {name}: FAILED ({exc}). If {host} blocks bots, paste the image address instead.")
            continue
        img = remove_background(raw)
        w, h = real_size(name, entry, scraped, img)
        src = ("products.txt" if entry.get("HEIGHT") or entry.get("WIDTH")
               else "website" if scraped.get("HEIGHT") or scraped.get("WIDTH") else "estimate")
        print(f"  {name}: {fmt_in(w)} W x {fmt_in(h)} H (size from {src})")
        parts[name] = {
            "img": img, "w": w, "h": h,
            "name": title, "finish": entry.get("FINISH", ""),
            "off_floor": to_inches(entry.get("OFF_FLOOR")),
        }

    if not parts:
        sys.exit("Nothing to draw. Fill in at least one URL in products.txt.")

    canvas, layout = draw_mockup(parts, settings)
    canvas.save(OUTPUT_FILE)
    print(f"\nSaved {OUTPUT_FILE}  ({canvas.width}x{canvas.height}px)")
    for name, bottom in layout.items():
        p = parts[name]
        print(f"  {name:<7} {fmt_in(p['w'])} W x {fmt_in(p['h'])} H, "
              f"bottom {fmt_in(bottom)} / top {fmt_in(bottom + p['h'])} off floor")


if __name__ == "__main__":
    main()

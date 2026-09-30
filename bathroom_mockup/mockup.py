"""Bathroom mockup builder.

Fill in products.txt (vanity, faucet, mirror links + finish), then run:

    python mockup.py

The script pulls each product photo from its page, removes the background,
scales every item to its real size in inches, and stacks them into one
transparent PNG (mockup.png): vanity on the floor, faucet on the counter,
mirror above.
"""

import io
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "products.txt"
OUTPUT_FILE = HERE / "mockup.png"

PIXELS_PER_INCH = 20          # output resolution
MARGIN_IN = 3                 # empty space around the mockup, inches
MIRROR_GAP_IN = 6             # space between faucet top and mirror bottom
MIRROR_MIN_BOTTOM_IN = 40     # mirror bottom never lower than this off the floor

# Used only when neither products.txt nor the website gives a size.
DEFAULT_HEIGHT_IN = {"VANITY": 34.5, "FAUCET": 7.0, "MIRROR": 30.0}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
ITEMS = ["VANITY", "FAUCET", "MIRROR"]


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


def to_inches(value):
    """Parse '34.5', '34 1/2', '34-1/2 in' into a float, or None."""
    if not value:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)(?:[\s-]+(\d+)/(\d+))?", str(value))
    if not m:
        return None
    number = float(m.group(1))
    if m.group(2):
        number += float(m.group(2)) / float(m.group(3))
    return number if 0.5 <= number <= 120 else None


# ---------------------------------------------------------------- fetching

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


DIM_PATTERNS = {
    "HEIGHT": [
        r"(?:overall\s+|product\s+|assembled\s+)?height[^0-9\n]{0,25}(\d+(?:\.\d+)?(?:[\s-]+\d+/\d+)?)\s*(?:in\b|inch|\"|'')",
        r"(\d+(?:\.\d+)?)\s*(?:in\.?|\"|inch(?:es)?)\s*(?:\(\s*)?h\b",
    ],
    "WIDTH": [
        r"(?:overall\s+|product\s+|assembled\s+)?width[^0-9\n]{0,25}(\d+(?:\.\d+)?(?:[\s-]+\d+/\d+)?)\s*(?:in\b|inch|\"|'')",
        r"(\d+(?:\.\d+)?)\s*(?:in\.?|\"|inch(?:es)?)\s*(?:\(\s*)?w\b",
    ],
}


def find_dimensions(soup):
    dims = {}
    for product in json_ld_products(soup):
        for key in ("HEIGHT", "WIDTH"):
            val = product.get(key.lower())
            if isinstance(val, dict):
                val = val.get("value")
            if key not in dims and to_inches(val):
                dims[key] = to_inches(val)
    text = soup.get_text(" ", strip=True).lower()
    for key, patterns in DIM_PATTERNS.items():
        for pattern in patterns:
            if key in dims:
                break
            for m in re.finditer(pattern, text):
                value = to_inches(m.group(1))
                if value:
                    dims[key] = value
                    break
    return dims


def download_image(url):
    img = Image.open(io.BytesIO(fetch(url).content))
    img.load()
    return img


def get_product(name, entry):
    url, finish = entry.get("URL", ""), entry.get("FINISH", "")
    resp = fetch(url)
    if resp.headers.get("Content-Type", "").startswith("image/"):
        print(f"  {name}: direct image link")
        return Image.open(io.BytesIO(resp.content)), {}

    soup = BeautifulSoup(resp.text, "html.parser")
    cands = collect_candidates(soup, resp.url)
    if not cands:
        raise RuntimeError("no product photo found on the page")

    if finish:
        ranked = sorted(cands, key=lambda c: finish_score(finish, c[1], c[0]), reverse=True)
        if finish_score(finish, ranked[0][1], ranked[0][0]) == 0:
            print(f"  {name}: WARNING - no photo labeled '{finish}', using the main photo")
        cands = ranked

    for img_url, _ in cands[:8]:
        try:
            img = download_image(img_url)
        except Exception:
            continue
        if min(img.size) >= 200:           # skip thumbnails
            print(f"  {name}: photo {img_url}")
            return img, find_dimensions(soup)
    raise RuntimeError("could not download a usable product photo")


# ---------------------------------------------------------------- images

def remove_background(img):
    img = img.convert("RGBA")
    alpha = np.array(img)[:, :, 3]
    if (alpha < 250).mean() > 0.05:        # already transparent
        return trim(img)

    try:
        from rembg import remove           # best quality, optional
        return trim(remove(img))
    except ImportError:
        pass

    # Fallback: flood-fill the plain studio background in from the edges.
    rgb = img.convert("RGB")
    filled = rgb.copy()
    w, h = rgb.size
    marker = (1, 254, 3)
    step = max(1, min(w, h) // 40)
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
    seeds += [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    for seed in seeds:
        if filled.getpixel(seed) != marker:
            ImageDraw.floodfill(filled, seed, marker, thresh=28)
    bg = np.all(np.array(filled) == marker, axis=2) & ~np.all(np.array(rgb) == marker, axis=2)
    mask = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8))
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1))
    img.putalpha(mask)
    return trim(img)


def trim(img):
    box = img.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox()
    return img.crop(box) if box else img


def real_size(name, entry, scraped, img):
    """Width/height in inches: products.txt > website > photo proportions > default."""
    h = to_inches(entry.get("HEIGHT")) or scraped.get("HEIGHT")
    w = to_inches(entry.get("WIDTH")) or scraped.get("WIDTH")
    aspect = img.width / img.height
    if h and not w:
        w = h * aspect
    elif w and not h:
        h = w / aspect
    elif not h and not w:
        h = DEFAULT_HEIGHT_IN[name]
        w = h * aspect
        print(f"  {name}: WARNING - no size found, guessing {h}\" tall. Set HEIGHT in products.txt.")
    return w, h


# ---------------------------------------------------------------- layout

def build_mockup(parts):
    """parts: {name: (image, width_in, height_in)} -> composed RGBA image, layout."""
    layout = {}
    floor = 0.0
    if "VANITY" in parts:
        _, w, h = parts["VANITY"]
        layout["VANITY"] = (w, h, floor)
        counter = floor + h
    else:
        counter = floor + DEFAULT_HEIGHT_IN["VANITY"]
    faucet_top = counter
    if "FAUCET" in parts:
        _, w, h = parts["FAUCET"]
        layout["FAUCET"] = (w, h, counter)
        faucet_top = counter + h
    if "MIRROR" in parts:
        _, w, h = parts["MIRROR"]
        layout["MIRROR"] = (w, h, max(faucet_top + MIRROR_GAP_IN, MIRROR_MIN_BOTTOM_IN))

    total_w = max(w for w, _, _ in layout.values()) + 2 * MARGIN_IN
    total_h = max(b + h for _, h, b in layout.values()) + 2 * MARGIN_IN
    ppi = PIXELS_PER_INCH
    canvas = Image.new("RGBA", (round(total_w * ppi), round(total_h * ppi)), (0, 0, 0, 0))

    for name in ["MIRROR", "VANITY", "FAUCET"]:   # faucet drawn last, on top
        if name not in layout:
            continue
        w, h, bottom = layout[name]
        img = parts[name][0].resize((max(1, round(w * ppi)), max(1, round(h * ppi))), Image.LANCZOS)
        x = round((total_w - w) / 2 * ppi)
        y = round((total_h - MARGIN_IN - bottom - h) * ppi)
        canvas.alpha_composite(img, (x, y))
    return canvas, layout


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else CONFIG_FILE
    entries = read_config(config_path)
    parts = {}
    for name in ITEMS:
        entry = entries.get(name, {})
        if not entry.get("URL"):
            print(f"  {name}: no URL, skipped")
            continue
        try:
            raw, scraped = get_product(name, entry)
        except Exception as exc:
            host = urlparse(entry["URL"]).netloc
            print(f"  {name}: FAILED ({exc}). If {host} blocks bots, paste the image address instead.")
            continue
        img = remove_background(raw)
        w, h = real_size(name, entry, scraped, img)
        parts[name] = (img, w, h)

    if not parts:
        sys.exit("Nothing to draw. Fill in at least one URL in products.txt.")

    canvas, layout = build_mockup(parts)
    canvas.save(OUTPUT_FILE)
    print(f"\nSaved {OUTPUT_FILE}  ({canvas.width}x{canvas.height}px, {PIXELS_PER_INCH}px = 1 inch)")
    for name, (w, h, bottom) in layout.items():
        print(f"  {name:<7} {w:5.1f}\" W x {h:5.1f}\" H, bottom {bottom:5.1f}\" off floor")


if __name__ == "__main__":
    main()

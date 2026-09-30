"""Tile layouts: every tile as a polygon, for any pattern and direction.

Tile size is long side L x short side S (inches). In "pattern space" the long
side runs along x; `swap` turns the layout 90 degrees (long side along y).
"""

import math
import random

import numpy as np
from PIL import Image, ImageDraw

PATTERNS = ["stack", "offset 1/2", "offset 1/3", "offset 1/4", "herringbone", "double herringbone",
            "chevron", "basketweave", "diagonal stack", "diagonal offset"]
PATTERN_ALIASES = {"offset": "offset 1/2", "running bond": "offset 1/2", "brick": "offset 1/2",
                   "stacked": "stack", "straight": "stack", "stack bond": "stack",
                   "diagonal": "diagonal stack", "diamond": "diagonal stack", "1/3 offset": "offset 1/3",
                   "1/2 offset": "offset 1/2", "1/4 offset": "offset 1/4"}
WALL_DIRECTIONS = ["horizontal", "vertical"]
FLOOR_DIRECTIONS = ["along room length", "across room width", "east-west", "north-south"]


def normalize_pattern(name):
    n = (name or "offset 1/2").strip().lower()
    n = PATTERN_ALIASES.get(n, n)
    return n if n in PATTERNS else "offset 1/2"


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _stack_rows(L, S, x0, y0, x1, y1, shift_frac):
    polys = []
    row = math.floor(y0 / S) - 1
    while row * S < y1:
        shift = (row * shift_frac * L) % L if shift_frac else 0
        col = math.floor((x0 - shift) / L) - 1
        while col * L + shift < x1:
            polys.append(rect(col * L + shift, row * S, L, S))
            col += 1
        row += 1
    return polys


def _herringbone(L, S, x0, y0, x1, y1):
    """Lattice (L, -L), (S, S); each cell = one horizontal + one vertical tile."""
    polys = []
    span = int((abs(x1 - x0) + abs(y1 - y0)) / S) + 2 * int(L / S) + 6
    for i in range(-span, span):
        for j in range(-span, span):
            ox, oy = i * L + j * S, -i * L + j * S
            if ox > x1 + L or oy > y1 + L or ox < x0 - 2 * L or oy < y0 - 2 * L:
                continue
            polys.append(rect(ox, oy, L, S))
            polys.append(rect(ox, oy + S, S, L))
    return polys


def _double_herringbone(L, S, x0, y0, x1, y1):
    polys = []
    for p in _herringbone(L, 2 * S, x0, y0, x1, y1):
        (ax, ay), (bx, by) = p[0], p[2]
        w, h = bx - ax, by - ay
        if w > h:       # horizontal pair: split along the long side
            polys += [rect(ax, ay, w, h / 2), rect(ax, ay + h / 2, w, h / 2)]
        else:
            polys += [rect(ax, ay, w / 2, h), rect(ax + w / 2, ay, w / 2, h)]
    return polys


def _chevron(L, S, x0, y0, x1, y1):
    """45-degree cut parallelograms zig-zagging in columns (long side along x)."""
    cw = L / math.sqrt(2)                 # column width
    h = S * math.sqrt(2)                  # vertical thickness of each piece
    polys = []
    for c in range(math.floor(x0 / cw) - 1, math.ceil(x1 / cw) + 1):
        xa, xb = c * cw, (c + 1) * cw
        up = c % 2 == 0
        k = math.floor((y0 - cw) / h) - 1
        while k * h < y1 + cw:
            y = k * h
            if up:
                polys.append([(xa, y + cw), (xb, y), (xb, y + h), (xa, y + cw + h)])
            else:
                polys.append([(xa, y), (xb, y + cw), (xb, y + cw + h), (xa, y + h)])
            k += 1
    return polys


def _basketweave(L, S, x0, y0, x1, y1):
    n = max(1, round(L / S))
    block = n * S
    polys = []
    for bi in range(math.floor(x0 / L) - 1, math.ceil(x1 / L) + 1):
        for bj in range(math.floor(y0 / block) - 1, math.ceil(y1 / block) + 1):
            bx, by = bi * L, bj * block
            if (bi + bj) % 2 == 0:
                polys += [rect(bx, by + k * S, L, S) for k in range(n)]
            else:
                w = L / n
                polys += [rect(bx + k * w, by, w, block) for k in range(n)]
    return polys


def tile_polys(pattern, L, S, w, h, swap=False, from_bottom=False):
    """Polygons (inches, y down, origin top-left of a w x h surface) covering the surface."""
    pattern = normalize_pattern(pattern)
    diagonal = pattern.startswith("diagonal")
    if pattern == "chevron":
        swap = not swap              # chevron columns run up; "horizontal" should point sideways
    base = pattern.replace("diagonal ", "") if diagonal else pattern
    if base == "offset":
        base = "offset 1/2"
    pw, ph = (h, w) if swap else (w, h)            # pattern space size

    if diagonal:
        d = (pw + ph) / math.sqrt(2) + 2 * L
        cx, cy = pw / 2, ph / 2
        area = (-d, -d, d, d)
    else:
        area = (0, 0, pw, ph)

    gen = {
        "stack": lambda *a: _stack_rows(L, S, *a, 0),
        "offset 1/2": lambda *a: _stack_rows(L, S, *a, 1 / 2),
        "offset 1/3": lambda *a: _stack_rows(L, S, *a, 1 / 3),
        "offset 1/4": lambda *a: _stack_rows(L, S, *a, 1 / 4),
        "herringbone": lambda *a: _herringbone(L, S, *a),
        "double herringbone": lambda *a: _double_herringbone(L, S, *a),
        "chevron": lambda *a: _chevron(L, S, *a),
        "basketweave": lambda *a: _basketweave(L, S, *a),
    }[base]
    polys = gen(*area)

    if diagonal:
        c, s = math.cos(math.pi / 4), math.sin(math.pi / 4)
        polys = [[(cx + x * c - y * s, cy + x * s + y * c) for x, y in p] for p in polys]
    if swap:
        polys = [[(y, x) for x, y in p] for p in polys]
    if from_bottom:
        polys = [[(x, h - y) for x, y in p] for p in polys]

    out = []
    for p in polys:                                  # keep only tiles that touch the surface
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        if max(xs) > 0 and min(xs) < w and max(ys) > 0 and min(ys) < h:
            out.append(p)
    return out


# ---------------------------------------------------------------- drawing

def spec_polys(spec, w_in, h_in, from_bottom=False, swap_extra=False):
    L, S = spec["tile"]
    return tile_polys(spec["pattern"], L, S, w_in, h_in, spec.get("swap", False) != swap_extra, from_bottom)


def tile_texture(spec, w_in, h_in, ppi, seed=1, from_bottom=False, swap_extra=False):
    """Tiled surface image (RGBA) w_in x h_in inches."""
    W, H = max(1, round(w_in * ppi)), max(1, round(h_in * ppi))
    out = Image.new("RGBA", (W, H), tuple(spec["grout"]) + (255,))
    rng = random.Random(seed)
    base = np.array(spec["color"], dtype=float)
    photo = spec.get("img")
    photo = photo.convert("RGB") if photo is not None else None
    grout_w = max(1, round(ppi * 0.125))
    cache = {}

    def face(size):
        """A tile face: a random crop of the tile photo (each tile a bit different), or a color."""
        key = size
        if key not in cache:
            variants = []
            for _ in range(6):
                k = rng.uniform(0.965, 1.035)
                if photo is not None:
                    pw, ph = photo.size
                    cw = min(pw, ph) * rng.uniform(0.45, 0.7)
                    chh = cw * size[1] / max(1, size[0])
                    if chh > ph:
                        chh, cw = ph * 0.9, ph * 0.9 * size[0] / max(1, size[1])
                    x = rng.uniform(0, max(0, pw - cw))
                    y = rng.uniform(0, max(0, ph - chh))
                    crop = photo.crop((int(x), int(y), int(x + cw), int(y + chh))).resize(size)
                    arr = np.asarray(crop, dtype=float) * 0.55 + base * 0.45
                else:
                    arr = np.ones((size[1], size[0], 3)) * base
                variants.append(Image.fromarray(np.clip(arr * k, 0, 255).astype(np.uint8)).convert("RGBA"))
            cache[key] = variants
        return rng.choice(cache[key])

    polys = spec_polys(spec, w_in, h_in, from_bottom, swap_extra)
    for p in polys:
        pts = [(x * ppi, y * ppi) for x, y in p]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        bx0, by0 = math.floor(min(xs)), math.floor(min(ys))
        size = (max(1, math.ceil(max(xs)) - bx0), max(1, math.ceil(max(ys)) - by0))
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).polygon([(x - bx0, y - by0) for x, y in pts], fill=255)
        out.paste(face(size), (bx0, by0), mask)
    draw = ImageDraw.Draw(out)
    for p in polys:
        pts = [(x * ppi, y * ppi) for x, y in p]
        draw.line(pts + [pts[0]], fill=tuple(spec["grout"]) + (255,), width=grout_w)
    return out


def tile_lines(img, box, spec, ppi, color, from_bottom=True, swap_extra=False):
    """Tile joint lines (line drawings) inside pixel box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = [round(v) for v in box]
    W, H = x1 - x0, y1 - y0
    if W < 2 or H < 2:
        return
    L, S = spec["tile"]
    if min(L, S) * ppi < 3:                      # joints would be a solid smear at this scale
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for p in spec_polys(spec, W / ppi, H / ppi, from_bottom, swap_extra):
        pts = [(x * ppi, y * ppi) for x, y in p]
        d.line(pts + [pts[0]], fill=color, width=1)
    img.alpha_composite(layer, (x0, y0))

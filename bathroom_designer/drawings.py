"""Floor plans and wall elevations, in two styles:
  "line"  - black-line construction drawing with dimensions (for the crew)
  "color" - filled with product photos, finishes, and tile (for the client)
"""

import math

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from products import fmt_dim
from project import FLOOR_KINDS, WALL_THICKNESS
from tiles import tile_lines, tile_texture

INK = (35, 33, 30, 255)
SOFT_INK = (120, 116, 110, 255)
GRID = (208, 203, 196, 255)
POCHE = (58, 56, 52, 255)
WHITE = (255, 255, 255, 255)
PORCELAIN = (248, 248, 246, 255)
COUNTER = (238, 235, 229, 255)
GLASS = (190, 214, 222, 80)
GLASS_LINE = (120, 160, 175, 255)
SHEET_DPI = 200          # drawings are made for sheets printed at this resolution
DIM_GAP_PX = 44          # distance between dimension tiers (0.22" on paper)

FINISH_COLORS = [
    ("matte black", (38, 37, 36)), ("black", (30, 30, 30)), ("brushed gold", (190, 158, 92)),
    ("brass", (184, 150, 82)), ("gold", (196, 162, 90)), ("champagne", (200, 178, 132)),
    ("bronze", (92, 66, 46)), ("brushed nickel", (170, 169, 163)), ("nickel", (182, 180, 172)),
    ("chrome", (200, 203, 207)), ("stainless", (190, 192, 194)), ("polished", (205, 205, 205)),
    ("white oak", (196, 164, 120)), ("walnut", (104, 70, 46)), ("oak", (176, 132, 86)),
    ("wood", (150, 108, 72)), ("teak", (150, 100, 58)), ("navy", (40, 54, 88)),
    ("blue", (70, 100, 140)), ("sage", (150, 164, 138)), ("green", (80, 110, 84)),
    ("charcoal", (62, 62, 64)), ("gray", (140, 140, 138)), ("grey", (140, 140, 138)),
    ("greige", (170, 162, 150)), ("beige", (214, 200, 178)), ("cream", (238, 230, 212)),
    ("marble", (236, 234, 230)), ("white", (244, 243, 240)),
]
METAL_DEFAULT = (190, 192, 194)


def finish_color(finish, default=(200, 196, 190)):
    f = (finish or "").lower()
    for key, rgb in FINISH_COLORS:
        if key in f:
            return rgb
    return default


def rgba(c, a=255):
    return tuple(c[:3]) + (a,)


def shade(c, k):
    return tuple(max(0, min(255, round(v * k))) for v in c[:3]) + (c[3] if len(c) > 3 else 255,)


def parse_color(value, default):
    try:
        return ImageColor.getrgb(value)[:3] if value else default
    except ValueError:
        return default


# ---------------------------------------------------------------- fonts & text

_FONT_FILES = {
    False: ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "Helvetica.ttc",
            "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    True: ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "Helvetica.ttc",
           "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def load_font(size, bold=False):
    for name in _FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, int(size), index=1 if bold and name.endswith(".ttc") else 0)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=int(size))
    except TypeError:                      # Pillow < 10.1
        return ImageFont.load_default()


def fonts(px):
    return {"small": load_font(px * 0.8), "normal": load_font(px), "bold": load_font(px, True),
            "big": load_font(px * 1.3, True)}


def text_w(font, s):
    b = font.getbbox(s)
    return b[2] - b[0]


def text_rotated(img, center, s, font, fill=INK):
    b = font.getbbox(s)
    t = Image.new("RGBA", (b[2] - b[0] + 4, b[3] - b[1] + 4), (0, 0, 0, 0))
    ImageDraw.Draw(t).text((2 - b[0], 2 - b[1]), s, font=font, fill=fill)
    t = t.rotate(90, expand=True)
    img.alpha_composite(t, (int(center[0] - t.width / 2), int(center[1] - t.height / 2)))


def dashed(draw, p1, p2, fill, width=1, dash=8, gap=6):
    (x1, y1), (x2, y2) = p1, p2
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    dx, dy = (x2 - x1) / length, (y2 - y1) / length
    pos = 0
    while pos < length:
        end = min(pos + dash, length)
        draw.line([x1 + dx * pos, y1 + dy * pos, x1 + dx * end, y1 + dy * end], fill=fill, width=width)
        pos = end + gap


def hdim(img, draw, x1, x2, y, label, font, ext_from=None, color=INK):
    """Horizontal dimension line at pixel height y from x1 to x2."""
    tick = 7
    for x in (x1, x2):
        if ext_from is not None:
            sign = 1 if y > ext_from else -1
            draw.line([x, ext_from + sign * 4, x, y + sign * 8], fill=color, width=1)
        draw.line([x - tick, y + tick, x + tick, y - tick], fill=color, width=2)
    draw.line([x1 - 6, y, x2 + 6, y], fill=color, width=1)
    if x2 - x1 > 14:
        draw.text(((x1 + x2) / 2, y - 5), label, font=font, fill=color, anchor="mb")


def vdim(img, draw, x, y1, y2, label, font, ext_from=None, color=INK):
    """Vertical dimension line at pixel x from y1 to y2 (text reads bottom-up)."""
    tick = 7
    for y in (y1, y2):
        if ext_from is not None:
            sign = 1 if x > ext_from else -1
            draw.line([ext_from + sign * 4, y, x + sign * 8, y], fill=color, width=1)
        draw.line([x - tick, y + tick, x + tick, y - tick], fill=color, width=2)
    draw.line([x, y1 - 6, x, y2 + 6], fill=color, width=1)
    if abs(y2 - y1) > 14:
        b = font.getbbox(label)
        text_rotated(img, (x - (b[3] - b[1]) / 2 - 7, (y1 + y2) / 2), label, font, color)


# ---------------------------------------------------------------- photos

def photo_width(item):
    """Width to draw the photo at: listed width, unless that would stretch the photo >15%."""
    if item.img is None:
        return item.w
    natural = item.h * item.img.width / item.img.height
    return item.w if 0.85 <= item.w / natural <= 1.15 else natural


def line_art(photo, size):
    """Turn a cut-out product photo into a black-line drawing on white."""
    img = photo.resize(size, Image.LANCZOS)
    alpha = img.getchannel("A")
    gray = img.convert("L").filter(ImageFilter.GaussianBlur(1))
    edges = gray.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 22 else 0)
    edges = ImageChops.multiply(edges, alpha.point(lambda a: 255 if a > 128 else 0))
    outline = alpha.point(lambda a: 255 if a > 128 else 0).filter(ImageFilter.FIND_EDGES)
    lines = ImageChops.lighter(edges, outline.point(lambda v: 255 if v > 0 else 0))
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", size, WHITE), mask=alpha.point(lambda a: 255 if a > 128 else 0))
    out.paste(Image.new("RGBA", size, INK), mask=lines)
    return out


def paste_photo(canvas, photo, box, shadow=False, floor_item=False):
    x0, y0, x1, y1 = [round(v) for v in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    img = photo.resize(size, Image.LANCZOS)
    if shadow:
        a = img.getchannel("A")
        pad = max(4, size[1] // 12)
        sh = Image.new("L", (size[0] + 4 * pad, size[1] + 4 * pad), 0)
        sh.paste(a, (2 * pad, 2 * pad))
        sh = sh.filter(ImageFilter.GaussianBlur(pad * 0.8)).point(lambda v: int(v * 0.30))
        dx, dy = (pad // 3, pad // 4) if floor_item else (pad // 2, pad)
        layer = Image.new("RGBA", sh.size, (20, 16, 12, 255))
        layer.putalpha(sh)
        canvas.alpha_composite(layer, (x0 - 2 * pad + dx, y0 - 2 * pad + dy))
    canvas.alpha_composite(img, (x0, y0))


# ---------------------------------------------------------------- elevation symbols

def metal(item):
    return rgba(item.color or finish_color(item.finish, METAL_DEFAULT))


def front_symbol(img, p, box, style, ppi, colors):
    """Generic front view of a fixture (used when there's no product photo)."""
    draw = ImageDraw.Draw(img)
    it, kind = p.item, p.kind
    x0, y0, x1, y1 = box
    color = style == "color"
    lw = max(1, round(ppi * 0.12))
    body = rgba(it.color or finish_color(it.finish)) if color else WHITE
    white = PORCELAIN if color else WHITE
    inch = ppi

    if kind == "vanity":
        top = y0 + 1.5 * inch
        kick = y1 - 4 * inch
        draw.rectangle([x0 + 3 * inch, kick, x1 - 3 * inch, y1], fill=shade(body, 0.7), outline=INK, width=lw)
        draw.rectangle([x0, top, x1, kick], fill=body, outline=INK, width=lw)
        draw.rectangle([x0 - 0.5 * inch, y0, x1 + 0.5 * inch, top], fill=COUNTER if color else WHITE,
                       outline=INK, width=lw)
        drawer_y = top + 7 * inch
        draw.line([x0, drawer_y, x1, drawer_y], fill=INK, width=lw)
        sinks = it.extra.get("sinks") or [p.u]
        n = len(sinks)
        doors = 2 * n
        for i in range(1, doors):
            x = x0 + (x1 - x0) * i / doors
            draw.line([x, drawer_y, x, kick], fill=INK, width=lw)
        pull = rgba(finish_color(it.finish, METAL_DEFAULT)) if not color else (180, 150, 90, 255)
        for i in range(doors):
            cx = x0 + (x1 - x0) * (i + 0.5) / doors
            draw.rectangle([cx - 2 * inch, (top + drawer_y) / 2 - 0.3 * inch,
                            cx + 2 * inch, (top + drawer_y) / 2 + 0.3 * inch], fill=pull)
            hx = cx + (1 if i % 2 == 0 else -1) * ((x1 - x0) / doors / 2 - 1.5 * inch)
            draw.rectangle([hx - 0.3 * inch, drawer_y + 3 * inch, hx + 0.3 * inch, drawer_y + 8 * inch], fill=pull)

    elif kind == "faucet":
        m = metal(it) if color else WHITE
        cx = (x0 + x1) / 2
        draw.rectangle([cx - 1.4 * inch, y1 - 0.8 * inch, cx + 1.4 * inch, y1], fill=m, outline=INK, width=lw)
        draw.rectangle([cx - 0.7 * inch, y0 + 1.2 * inch, cx + 0.7 * inch, y1], fill=m, outline=INK, width=lw)
        draw.ellipse([cx - 1.3 * inch, y0, cx + 1.3 * inch, y0 + 2.4 * inch], fill=m, outline=INK, width=lw)
        if (x1 - x0) > 5 * inch:
            for hx in (x0 + 0.8 * inch, x1 - 0.8 * inch):
                draw.rectangle([hx - 0.6 * inch, y1 - 3.5 * inch, hx + 0.6 * inch, y1], fill=m, outline=INK, width=lw)

    elif kind == "mirror":
        frame = rgba(it.color or finish_color(it.finish, (60, 58, 55))) if color else WHITE
        draw.rectangle(box, fill=frame, outline=INK, width=lw)
        inner = [x0 + inch, y0 + inch, x1 - inch, y1 - inch]
        draw.rectangle(inner, fill=(214, 224, 229, 255) if color else WHITE, outline=INK, width=lw)
        w = inner[2] - inner[0]
        for k in (0.18, 0.28):
            draw.line([inner[0] + w * k, inner[1] + w * 0.08, inner[0] + w * (k + 0.12), inner[1] + w * 0.2],
                      fill=(255, 255, 255, 200) if color else SOFT_INK, width=lw)

    elif kind == "sconce":
        m = metal(it) if color else WHITE
        cx = (x0 + x1) / 2
        draw.rectangle([cx - 1 * inch, y0 + (y1 - y0) * 0.35, cx + 1 * inch, y1 - (y1 - y0) * 0.1],
                       fill=m, outline=INK, width=lw)
        draw.rounded_rectangle([x0, y0, x1, y0 + (y1 - y0) * 0.6], radius=inch,
                               fill=(250, 244, 226, 255) if color else WHITE, outline=INK, width=lw)

    elif kind == "vanity_light":
        m = metal(it) if color else WHITE
        draw.rectangle([x0, y0, x1, y0 + 1.5 * inch], fill=m, outline=INK, width=lw)
        n = 3 if (x1 - x0) < 36 * inch else 4
        for i in range(n):
            cx = x0 + (x1 - x0) * (i + 0.5) / n
            draw.rounded_rectangle([cx - 2.5 * inch, y0 + 2 * inch, cx + 2.5 * inch, y1], radius=inch,
                                   fill=(250, 244, 226, 255) if color else WHITE, outline=INK, width=lw)

    elif kind == "toilet" and it.extra.get("type") == "wall-hung":
        w = x1 - x0
        cx = (x0 + x1) / 2
        # flush plate at ~40" and the in-wall carrier (dashed)
        plate_y = y1 - (40 - p.bottom) * inch
        if not color:
            for a, b in (((x0, y0 - 30 * inch), (x1, y0 - 30 * inch)), ((x0, y0 - 30 * inch), (x0, y1)),
                         ((x1, y0 - 30 * inch), (x1, y1))):
                dashed(draw, a, b, SOFT_INK)
        draw.rounded_rectangle([cx - 4.5 * inch, plate_y - 3 * inch, cx + 4.5 * inch, plate_y + 3 * inch],
                               radius=0.5 * inch, fill=metal(it) if color else WHITE, outline=INK, width=lw)
        draw.line([cx, plate_y - 3 * inch, cx, plate_y + 3 * inch], fill=INK, width=1)
        draw.rounded_rectangle([x0, y0, x1, y0 + 1.5 * inch], radius=0.6 * inch, fill=white, outline=INK, width=lw)
        draw.polygon([(x0 + w * 0.04, y0 + 1.5 * inch), (x1 - w * 0.04, y0 + 1.5 * inch),
                      (cx + w * 0.3, y1), (cx - w * 0.3, y1)], fill=white, outline=INK)

    elif kind == "toilet":
        w = x1 - x0
        cx = (x0 + x1) / 2
        tank_bottom = y0 + 15 * inch
        draw.rounded_rectangle([x0 + w * 0.05, y0, x1 - w * 0.05, tank_bottom], radius=inch,
                               fill=white, outline=INK, width=lw)
        seat = y1 - 15.5 * inch
        draw.rounded_rectangle([x0, seat, x1, seat + 1.5 * inch], radius=0.6 * inch,
                               fill=white, outline=INK, width=lw)
        draw.polygon([(x0 + w * 0.08, seat + 1.5 * inch), (x1 - w * 0.08, seat + 1.5 * inch),
                      (cx + w * 0.24, y1), (cx - w * 0.24, y1)], fill=white, outline=INK)
        if it.extra.get("type") == "two-piece":         # separate tank sits on the bowl
            draw.line([x0 + w * 0.12, tank_bottom + 0.8 * inch, x1 - w * 0.12, tank_bottom + 0.8 * inch],
                      fill=INK, width=lw)
            draw.ellipse([x1 - w * 0.25, y0 + 2 * inch, x1 - w * 0.12, y0 + 3.2 * inch], fill=metal(it), outline=INK)

    elif kind == "towel_bar":
        m = metal(it) if color else WHITE
        post = 1.2 * inch
        if color:                                      # a folded towel hanging on the bar
            tw = (x1 - x0) * 0.72
            cx = (x0 + x1) / 2
            draw.rectangle([cx - tw / 2, y0 + 0.8 * inch, cx + tw / 2, y0 + 20 * inch], fill=TOWEL, outline=(205, 200, 190))
            draw.line([cx - tw / 2, y0 + 17 * inch, cx + tw / 2, y0 + 17 * inch], fill=(215, 210, 200), width=lw)
        for px in (x0, x1 - post):
            draw.rectangle([px, y0, px + post, y1], fill=m, outline=INK, width=lw)
        draw.rectangle([x0 + post, y0 + 0.6 * inch, x1 - post, y0 + 1.3 * inch], fill=m, outline=INK, width=1)

    elif kind == "towel_ring":
        m = metal(it) if color else WHITE
        cx = (x0 + x1) / 2
        draw.rectangle([cx - 1 * inch, y0, cx + 1 * inch, y0 + 2 * inch], fill=m, outline=INK, width=lw)
        r = (x1 - x0) / 2
        if color:
            draw.polygon([(cx - r * 0.6, y1 - r * 0.2), (cx + r * 0.6, y1 - r * 0.2),
                          (cx + r * 0.8, y1 + 10 * inch), (cx - r * 0.8, y1 + 10 * inch)], fill=TOWEL, outline=(205, 200, 190))
        draw.ellipse([cx - r, y1 - 2 * r, cx + r, y1], outline=m if color else INK, width=max(lw, round(0.5 * inch)))
        if color:
            draw.ellipse([cx - r, y1 - 2 * r, cx + r, y1], outline=INK, width=1)

    elif kind == "robe_hook":
        m = metal(it) if color else WHITE
        cx = (x0 + x1) / 2
        draw.ellipse([cx - (x1 - x0) / 2, y0, cx + (x1 - x0) / 2, y0 + (x1 - x0)], fill=m, outline=INK, width=lw)
        draw.line([cx, y0 + (x1 - x0) / 2, cx, y1 - 0.5 * inch], fill=INK if not color else m, width=max(lw, round(0.5 * inch)))
        draw.ellipse([cx - 0.5 * inch, y1 - 1 * inch, cx + 0.5 * inch, y1], fill=m, outline=INK)

    elif kind == "tp_holder":
        m = metal(it) if color else WHITE
        cx = (x0 + x1) / 2
        draw.rectangle([x0, y0, x0 + 1.2 * inch, y1], fill=m, outline=INK, width=lw)
        draw.rectangle([x0 + 1.2 * inch, y0 + 0.9 * inch, x1, y0 + 1.6 * inch], fill=m, outline=INK, width=1)
        draw.ellipse([x0 + 1.4 * inch, y0 - 1.2 * inch, x1 - 0.2 * inch, y1 + 1.2 * inch],
                     fill=(252, 252, 250, 255) if color else WHITE, outline=INK, width=lw)

    elif kind == "tub":
        if it.extra.get("type") == "freestanding":
            r = min((x1 - x0) / 4, (y1 - y0) * 0.55)
            try:
                draw.rounded_rectangle(box, radius=r, fill=white, outline=INK, width=lw,
                                       corners=(True, True, False, False))
            except TypeError:                   # Pillow < 10
                draw.rounded_rectangle(box, radius=r, fill=white, outline=INK, width=lw)
            draw.line([x0 + r * 0.3, y0 + 1.5 * inch, x1 - r * 0.3, y0 + 1.5 * inch], fill=INK, width=1)
        else:
            draw.rectangle(box, fill=white, outline=INK, width=lw)
            draw.line([x0, y0 + 2 * inch, x1, y0 + 2 * inch], fill=INK, width=lw)

    elif kind == "shower":
        curb_top = y1 - 4 * inch
        draw.rectangle([x0, curb_top, x1, y1], fill=COUNTER if color else WHITE, outline=INK, width=lw)
        glass = [x0, y0, x1, curb_top]
        if color:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).rectangle(glass, fill=GLASS)
            img.alpha_composite(layer)
        draw.rectangle(glass, outline=GLASS_LINE if color else INK, width=lw)
        mid = (x0 + x1) / 2
        draw.line([mid, y0, mid, curb_top], fill=GLASS_LINE if color else INK, width=lw)
        handle = rgba(finish_color(it.finish, METAL_DEFAULT))
        draw.rectangle([mid - 2 * inch, (y0 + curb_top) / 2 - 6 * inch, mid - 1.2 * inch,
                        (y0 + curb_top) / 2 + 6 * inch], fill=handle if color else WHITE, outline=INK)
        if not color:
            w = (x1 - x0)
            for k in (0.15, 0.22, 0.65, 0.72):
                draw.line([x0 + w * k, y0 + 6 * inch, x0 + w * k + 6 * inch, y0], fill=SOFT_INK, width=1)

    elif kind == "shower_trim":
        m = metal(it) if color else WHITE
        cx = (x0 + x1) / 2
        draw.line([cx, y0 + 2 * inch, cx, y1 - 3.5 * inch], fill=m if color else INK, width=max(lw, round(0.8 * inch)))
        if color:
            draw.line([cx, y0 + 2 * inch, cx, y1 - 3.5 * inch], fill=INK, width=1)
        draw.ellipse([cx - 3.5 * inch, y1 - 7 * inch, cx + 3.5 * inch, y1], fill=m, outline=INK, width=lw)
        draw.ellipse([cx - 1 * inch, y1 - 4.5 * inch, cx + 1 * inch, y1 - 2.5 * inch], fill=shade(m, 0.8), outline=INK)
        draw.rectangle([cx - 4 * inch, y0, cx + 4 * inch, y0 + 1.5 * inch], fill=m, outline=INK, width=lw)

    elif kind == "door":
        trim = rgba(colors["trim"]) if color else WHITE
        draw.rectangle([x0 - 3 * inch, y0 - 3 * inch, x1 + 3 * inch, y1], fill=trim, outline=INK, width=lw)
        leaf = rgba(it.color or finish_color(it.finish, colors["trim"])) if color else WHITE
        draw.rectangle(box, fill=leaf, outline=INK, width=lw)
        w = x1 - x0
        for top, bottom in ((y0 + 4 * inch, y0 + 36 * inch), (y0 + 40 * inch, y1 - 6 * inch)):
            draw.rectangle([x0 + 4 * inch, top, x1 - 4 * inch, bottom], outline=INK, width=lw)
        knob_x = x0 + 2.5 * inch if it.extra.get("swing", "right") == "right" else x1 - 2.5 * inch
        draw.ellipse([knob_x - 1.1 * inch, y1 - 37 * inch, knob_x + 1.1 * inch, y1 - 35 * inch],
                     fill=(170, 150, 110, 255) if color else WHITE, outline=INK)
        del w

    elif kind == "window":
        trim = rgba(colors["trim"]) if color else WHITE
        draw.rectangle([x0 - 3 * inch, y0 - 3 * inch, x1 + 3 * inch, y1 + 3 * inch], fill=trim, outline=INK, width=lw)
        draw.rectangle([x0 - 4 * inch, y1 + 2 * inch, x1 + 4 * inch, y1 + 3.2 * inch], fill=trim, outline=INK, width=lw)
        draw.rectangle(box, fill=trim, outline=INK, width=lw)
        glass = [x0 + 2 * inch, y0 + 2 * inch, x1 - 2 * inch, y1 - 2 * inch]
        draw.rectangle(glass, fill=(196, 216, 226, 255) if color else WHITE, outline=INK, width=lw)
        draw.line([x0, (y0 + y1) / 2, x1, (y0 + y1) / 2], fill=INK, width=lw * 2)
    else:
        draw.rectangle(box, fill=body, outline=INK, width=lw)


def side_symbol(draw, p, box, wall_at_left, style, ppi):
    """Side view of a floor fixture standing against the neighboring wall."""
    it, kind = p.item, p.kind
    x0, y0, x1, y1 = box
    color = style == "color"
    lw = max(1, round(ppi * 0.12))
    inch = ppi
    white = PORCELAIN if color else WHITE
    body = rgba(it.color or finish_color(it.finish)) if color else WHITE

    def near(b0, b1):                       # distance-from-its-wall range -> x range
        return (x0 + b0 * inch, x0 + b1 * inch) if wall_at_left else (x1 - b1 * inch, x1 - b0 * inch)

    if kind == "vanity":
        draw.rectangle([x0, y0 + 1.5 * inch, x1, y1 - 4 * inch], fill=body, outline=INK, width=lw)
        kx = near(0, it.d - 3)
        draw.rectangle([kx[0], y1 - 4 * inch, kx[1], y1], fill=shade(body, 0.7), outline=INK, width=lw)
        draw.rectangle([x0 - 0.5 * inch, y0, x1 + 0.5 * inch, y0 + 1.5 * inch], fill=COUNTER if color else WHITE,
                       outline=INK, width=lw)
    elif kind == "toilet" and it.extra.get("type") == "wall-hung":
        bx = near(0, it.d)
        draw.rounded_rectangle([bx[0], y0, bx[1], y1], radius=4 * inch, fill=white, outline=INK, width=lw)
    elif kind == "toilet":
        tx = near(0, 9)
        draw.rounded_rectangle([tx[0], y0, tx[1], y0 + 15 * inch], radius=inch, fill=white, outline=INK, width=lw)
        bx = near(6, it.d)
        draw.rounded_rectangle([bx[0], y1 - 16 * inch, bx[1], y1], radius=4 * inch, fill=white, outline=INK, width=lw)
    elif kind == "tub":
        draw.rectangle(box, fill=white, outline=INK, width=lw)
        draw.line([x0, y0 + 2 * inch, x1, y0 + 2 * inch], fill=INK, width=lw)
    elif kind == "shower":
        draw.rectangle([x0, y1 - 4 * inch, x1, y1], fill=COUNTER if color else WHITE, outline=INK, width=lw)
        gx = near(it.d - 0.5, it.d)
        draw.rectangle([gx[0] - 1, y0, gx[1] + 1, y1 - 4 * inch], fill=GLASS_LINE if color else INK)


def plan_rect_on_wall(room, p, wall):
    """Where a fixture's plan footprint lands on `wall`: (u0, u1, nearest distance)."""
    x0, y0, x1, y1 = p.footprint(room)
    pts = [room.to_wall(wall, x, y) for x, y in ((x0, y0), (x1, y1))]
    us = sorted(u for u, _ in pts)
    return us[0], us[1], min(b for _, b in pts)


# ---------------------------------------------------------------- elevation

ELEV_MARGINS = {"left": 1.05, "right": 0.45, "top": 0.1, "bottom": 0.6}   # paper inches, with dimensions
FRONT_ORDER = ["tub", "vanity", "toilet", "mirror", "vanity_light", "sconce", "tp_holder", "towel_bar",
               "towel_ring", "robe_hook", "faucet", "shower_trim", "shower"]
TOWEL = (238, 234, 226, 255)


def paper_margins(ppi):
    """Margins in real inches that come out the same size on paper at any scale."""
    return {k: v * SHEET_DPI / ppi for k, v in ELEV_MARGINS.items()}


def elevation_extent(room, wall, ppi):
    m = paper_margins(ppi)
    return room.wall_len(wall) + m["left"] + m["right"], room.ceiling + m["top"] + m["bottom"]


def draw_elevation(room, wall, placed, tiles, colors, style, ppi, fnt=None, dims=True,
                   flat_only=False, shadows=False):
    Lw, H = room.wall_len(wall), room.ceiling
    m = paper_margins(ppi) if dims else {"left": 0, "right": 0, "top": 0, "bottom": 0}
    W_px = round((Lw + m["left"] + m["right"]) * ppi)
    H_px = round((H + m["top"] + m["bottom"]) * ppi)
    color = style == "color"
    img = Image.new("RGBA", (W_px, H_px), WHITE if dims else (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ox = m["left"] * ppi
    floor_y = (m["top"] + H) * ppi

    def X(u):
        return ox + u * ppi

    def Y(v):
        return floor_y - v * ppi

    draw.rectangle([X(0), Y(H), X(Lw), Y(0)], fill=rgba(colors["wall"]) if color else WHITE)

    # --- tile zones
    zones = []
    wt = tiles.get("WALL_TILE")
    if wt and wt["height"] != 0:
        zones.append((0, Lw, min(H, wt["height"] or H), wt))
    st = tiles.get("SHOWER_TILE") or wt
    for p in placed:
        if p.kind in ("shower", "tub") and st and not (p.kind == "tub" and p.item.extra.get("type") == "freestanding"):
            u0, u1, near = plan_rect_on_wall(room, p, wall)
            if near < 1:
                zones.append((max(0, u0), min(Lw, u1), min(H, st["height"] or H), st))
    for u0, u1, top, spec in zones:
        box = [X(u0), Y(top), X(u1), Y(0)]
        if color:
            tex = tile_texture(spec, u1 - u0, top, ppi, seed=hash((wall, u0)) % 1000, from_bottom=True)
            img.paste(tex, (round(box[0]), round(box[1])))
        else:
            tile_lines(img, box, spec, ppi, GRID)
            draw.rectangle(box, outline=SOFT_INK, width=1)

    # --- doors & windows in this wall
    for p in placed:
        if p.wall == wall and p.kind in ("door", "window") and p.x is None:
            box = [X(p.u - p.item.w / 2), Y(p.top), X(p.u + p.item.w / 2), Y(p.bottom)]
            front_symbol(img, p, box, style, ppi, colors)
            draw = ImageDraw.Draw(img)

    # --- floor fixtures against the neighboring walls, seen from the side
    if not flat_only:
        for p in placed:
            if p.kind in FLOOR_KINDS and p.wall != wall and p.x is None:
                u0, u1, near = plan_rect_on_wall(room, p, wall)
                if near < 1 and u1 > 0 and u0 < Lw:
                    wall_at_left = p.wall == room.left_wall(wall)
                    box = [X(max(0, u0)), Y(p.top), X(min(Lw, u1)), Y(p.bottom)]
                    side_symbol(draw, p, box, wall_at_left, style, ppi)

    # --- fixtures on this wall, front view
    own = [p for p in placed if p.wall == wall and p.x is None and p.kind in FRONT_ORDER]
    own.sort(key=lambda p: FRONT_ORDER.index(p.kind))
    for p in own:
        if flat_only and p.kind in FLOOR_KINDS:
            continue
        it = p.item
        w = photo_width(it) if it.img is not None else it.w
        box = [X(p.u - w / 2), Y(p.top), X(p.u + w / 2), Y(p.bottom)]
        if it.img is not None and p.kind != "shower":
            size = (max(1, round(box[2] - box[0])), max(1, round(box[3] - box[1])))
            if color:
                paste_photo(img, it.img, box, shadow=shadows, floor_item=p.kind in FLOOR_KINDS)
            else:
                img.alpha_composite(line_art(it.img, size), (round(box[0]), round(box[1])))
        elif it.img is not None and color:          # shower photo: glass over the tile
            photo = it.img.copy()
            photo.putalpha(photo.getchannel("A").point(lambda a: int(a * 0.75)))
            paste_photo(img, photo, box)
        else:
            front_symbol(img, p, box, style, ppi, colors)
        draw = ImageDraw.Draw(img)

    # --- cut walls, floor, and ceiling (section style)
    if dims:
        t = WALL_THICKNESS
        for rect in ([X(-t), Y(H + 4), X(0), Y(-4)], [X(Lw), Y(H + 4), X(Lw + t), Y(-4)],
                     [X(-t), Y(0), X(Lw + t), Y(-4)], [X(-t), Y(H + 4), X(Lw + t), Y(H)]):
            draw.rectangle(rect, fill=POCHE)
        draw.rectangle([X(0), Y(H), X(Lw), Y(0)], outline=INK, width=max(2, round(ppi * 0.2)))
        if not color:
            elevation_dims(img, draw, room, wall, placed, X, Y, fnt, ppi, tiles)
        else:
            vdim(img, draw, X(Lw + t) + DIM_GAP_PX, Y(0), Y(H), fmt_dim(H), fnt["small"], ext_from=X(Lw + t))
            hdim(img, draw, X(0), X(Lw), Y(-4) + DIM_GAP_PX, fmt_dim(Lw), fnt["small"], ext_from=Y(-4))
    return img


def elevation_dims(img, draw, room, wall, placed, X, Y, fnt, ppi, tiles):
    Lw, H = room.wall_len(wall), room.ceiling
    own = [p for p in placed if p.wall == wall and p.x is None]

    # bottom: chain to item centerlines, then overall
    points = {0.0, Lw}
    for p in own:
        if p.kind in ("vanity", "toilet", "tub", "shower", "door", "window", "shower_trim", "faucet",
                      "towel_bar", "towel_ring", "robe_hook", "tp_holder"):
            points.add(round(p.u, 2))
    pts = sorted(points)
    chain_y = Y(-4) + DIM_GAP_PX
    for a, b in zip(pts, pts[1:]):
        if b - a > 0.4:
            hdim(img, draw, X(a), X(b), chain_y, fmt_dim(b - a), fnt["small"], ext_from=Y(-4))
    if len(pts) > 2:
        hdim(img, draw, X(0), X(Lw), chain_y + DIM_GAP_PX, fmt_dim(Lw), fnt["small"], ext_from=chain_y)
    vdim(img, draw, X(Lw + WALL_THICKNESS) + DIM_GAP_PX * 1.3, Y(0), Y(H), fmt_dim(H) + " CLG", fnt["small"],
         ext_from=X(Lw + WALL_THICKNESS))

    # left: heights of the things a crew needs to rough in
    marks = []
    for p in own:
        k = p.kind
        if k == "vanity":
            marks.append((p.top, "counter"))
        elif k == "mirror":
            marks += [(p.bottom, "mirror bot."), (p.top, "mirror top")]
        elif k == "sconce":
            marks.append((p.bottom + p.item.h / 2, "sconce CL"))
        elif k == "vanity_light":
            marks.append((p.bottom + p.item.h / 2, "light CL"))
        elif k == "window":
            marks += [(p.bottom, "sill"), (p.top, "window head")]
        elif k == "door":
            marks.append((p.top, "door head"))
        elif k == "shower_trim":
            marks += [(p.bottom + 3.5, "valve CL"), (p.top, "shower head")]
        elif k == "tub":
            marks.append((p.top, "tub rim"))
        elif k == "toilet":
            if p.item.extra.get("type") == "wall-hung":
                marks += [(p.top, "toilet rim"), (40, "flush plate CL")]
            else:
                marks.append((p.top, "toilet top"))
        elif k in ("towel_bar", "towel_ring", "robe_hook", "tp_holder"):
            marks.append((p.bottom + p.item.h / 2, {"towel_bar": "towel bar CL", "towel_ring": "towel ring CL",
                                                    "robe_hook": "robe hook CL", "tp_holder": "TP holder CL"}[k]))
    wt = tiles.get("WALL_TILE")
    if wt and wt["height"] and wt["height"] < H:
        marks.append((wt["height"], "tile"))
    seen, uniq = set(), []
    for v, name in sorted(marks):
        if round(v * 2) not in seen:
            seen.add(round(v * 2))
            uniq.append((v, name))
    line_x = X(-WALL_THICKNESS) - 24
    if uniq:
        draw.line([line_x, Y(0), line_x, Y(max(v for v, _ in uniq))], fill=INK, width=1)
    last_y = None
    for v, name in uniq:
        y = Y(v)
        draw.line([line_x - 8, y, line_x + 8, y], fill=INK, width=2)
        draw.line([line_x + 8, y, X(0), y], fill=GRID, width=1)
        if last_y is not None and last_y - y < fnt["small"].size * 2.1:
            continue
        draw.text((line_x - 12, y + 1), fmt_dim(v), font=fnt["small"], fill=INK, anchor="rb")
        draw.text((line_x - 12, y + 3), name, font=fnt["small"], fill=SOFT_INK, anchor="rt")
        last_y = y


# ---------------------------------------------------------------- floor plan

PLAN_MARGIN_PAPER = 1.0
PLAN_LABELS = {"vanity": "VANITY", "toilet": "WC", "tub": "TUB", "shower": "SHOWER"}


def plan_extent(room, ppi):
    m = PLAN_MARGIN_PAPER * SHEET_DPI / ppi
    return room.width + 2 * m, room.length + 2 * m


def draw_plan(room, placed, tiles, colors, style, ppi, fnt, dims=True):
    M = PLAN_MARGIN_PAPER * SHEET_DPI / ppi if dims else WALL_THICKNESS + 2
    W_px = round((room.width + 2 * M) * ppi)
    H_px = round((room.length + 2 * M) * ppi)
    color = style == "color"
    img = Image.new("RGBA", (W_px, H_px), WHITE)
    draw = ImageDraw.Draw(img)
    lw = max(1, round(ppi * 0.15))

    def P(x, y):
        return (M + x) * ppi, (M + y) * ppi

    room_box = [*P(0, 0), *P(room.width, room.length)]
    ft = tiles.get("FLOOR_TILE")
    if color and ft:
        img.paste(tile_texture(ft, room.width, room.length, ppi, seed=7), (round(room_box[0]), round(room_box[1])))
        tile_lines(img, room_box, ft, ppi, (110, 102, 92, 120), from_bottom=False)   # joints readable at scale
    elif ft:
        tile_lines(img, room_box, ft, ppi, (200, 195, 188, 255), from_bottom=False)
        draw = ImageDraw.Draw(img)

    # soft shadows under fixtures (color)
    if color:
        sh = Image.new("L", img.size, 0)
        sd = ImageDraw.Draw(sh)
        for p in placed:
            if p.kind in FLOOR_KINDS:
                x0, y0, x1, y1 = p.footprint(room)
                a, b = P(x0 + 1, y0 + 1.5), P(x1 + 1, y1 + 1.5)
                sd.rectangle([a[0], a[1], b[0], b[1]], fill=110)
        sh = sh.filter(ImageFilter.GaussianBlur(ppi * 1.5))
        layer = Image.new("RGBA", img.size, (30, 24, 18, 255))
        layer.putalpha(sh)
        img.alpha_composite(layer)
        draw = ImageDraw.Draw(img)

    for p in placed:
        if p.kind in FLOOR_KINDS:
            plan_symbol(img, draw, room, p, P, style, ppi, lw, tiles)

    # walls (poché) and openings
    t = WALL_THICKNESS
    for rect in ((-t, -t, room.width + t, 0), (-t, room.length, room.width + t, room.length + t),
                 (-t, 0, 0, room.length), (room.width, 0, room.width + t, room.length)):
        a, b = P(rect[0], rect[1]), P(rect[2], rect[3])
        draw.rectangle([a[0], a[1], b[0], b[1]], fill=POCHE)
    for p in placed:
        if p.kind in ("door", "window") and p.x is None:
            plan_opening(img, draw, room, p, P, style, ppi, lw, colors)

    for p in placed:
        if p.kind == "ceiling_light":
            cx, cy = P(p.x, p.y)
            r = p.item.w / 2 * ppi
            for i in range(0, 360, 20):
                a1, a2 = math.radians(i), math.radians(i + 10)
                draw.line([cx + r * math.cos(a1), cy + r * math.sin(a1), cx + r * math.cos(a2), cy + r * math.sin(a2)],
                          fill=SOFT_INK, width=lw)
            d = r * 0.5
            draw.line([cx - d, cy - d, cx + d, cy + d], fill=SOFT_INK, width=lw)
            draw.line([cx - d, cy + d, cx + d, cy - d], fill=SOFT_INK, width=lw)

    # labels
    for p in placed:
        if p.kind in PLAN_LABELS:
            x0, y0, x1, y1 = p.footprint(room)
            cx, cy = P((x0 + x1) / 2, (y0 + y1) / 2)
            label = PLAN_LABELS[p.kind]
            f = fnt["small"]
            if p.kind == "vanity":
                cx, cy = P(*room.to_plan(p.wall, p.u, p.item.d * 0.8))
            if color:
                tw = text_w(f, label)
                draw.rounded_rectangle([cx - tw / 2 - 5, cy - f.size / 2 - 4, cx + tw / 2 + 5, cy + f.size / 2 + 4],
                                       radius=4, fill=(255, 255, 255, 200))
            draw.text((cx, cy), label, font=f, fill=INK, anchor="mm")

    if dims:
        if not color:
            plan_dims(img, draw, room, placed, P, fnt, ppi)
        ax, ay = P(room.width + t, -t)
        north_arrow(draw, (ax + 60, max(ay - 45, 70)), fnt)
    return img


def plan_symbol(img, draw, room, p, P, style, ppi, lw, tiles):
    it = p.item
    color = style == "color"
    white = PORCELAIN if color else WHITE
    left = p.u - it.w / 2

    def R(a0, b0, a1, b1):
        pa = P(*room.to_plan(p.wall, left + a0, p.off_wall + b0))
        pb = P(*room.to_plan(p.wall, left + a1, p.off_wall + b1))
        return [min(pa[0], pb[0]), min(pa[1], pb[1]), max(pa[0], pb[0]), max(pa[1], pb[1])]

    w, d = it.w, it.d
    if p.kind == "vanity":
        body = rgba(it.color or finish_color(it.finish)) if color else WHITE
        draw.rectangle(R(0, 0, w, d), fill=body, outline=INK, width=lw)
        draw.rectangle(R(0.6, 0, w - 0.6, d - 0.6), fill=COUNTER if color else WHITE, outline=INK, width=1)
        for su in it.extra.get("sinks") or [p.u]:
            a = su - left
            draw.ellipse(R(a - 8.5, d / 2 - 5.5, a + 8.5, d / 2 + 7.5), fill=WHITE, outline=INK, width=lw)
            draw.ellipse(R(a - 0.8, d / 2 + 0.2, a + 0.8, d / 2 + 1.8), outline=INK, width=1)
            draw.ellipse(R(a - 1, 1.2, a + 1, 3.2), fill=rgba(finish_color("", METAL_DEFAULT)), outline=INK)
    elif p.kind == "toilet":
        kind = it.extra.get("type", "one-piece")
        if kind == "wall-hung":                   # tank is in the wall: just a flush plate line
            draw.rectangle(R(w / 2 - 4.5, 0, w / 2 + 4.5, 0.8), fill=INK)
            draw.ellipse(R(w / 2 - 7, 0, w / 2 + 7, d), fill=white, outline=INK, width=lw)
            draw.ellipse(R(w / 2 - 4.8, 3, w / 2 + 4.8, d - 3), outline=INK, width=1)
        else:
            tank = 8.5 if kind == "two-piece" else 9.5
            draw.ellipse(R(w / 2 - 7.5, tank - 2, w / 2 + 7.5, d), fill=white, outline=INK, width=lw)
            draw.rounded_rectangle(R(w / 2 - 10, 0, w / 2 + 10, tank), radius=ppi * (1 if kind == "two-piece" else 3),
                                   fill=white, outline=INK, width=lw)
            draw.ellipse(R(w / 2 - 5, tank + 1.5, w / 2 + 5, d - 3), outline=INK, width=1)
    elif p.kind == "tub":
        if it.extra.get("type") == "freestanding":
            r = min(w, d) / 2 * ppi
            draw.rounded_rectangle(R(0, 0, w, d), radius=r, fill=white, outline=INK, width=lw)
            draw.rounded_rectangle(R(3, 3, w - 3, d - 3), radius=max(1, r - 3 * ppi),
                                   fill=(236, 242, 244, 255) if color else WHITE, outline=INK, width=1)
            draw.ellipse(R(w / 2 - 1, d / 2 - 1, w / 2 + 1, d / 2 + 1), outline=INK)
        else:
            draw.rectangle(R(0, 0, w, d), fill=white, outline=INK, width=lw)
            draw.rounded_rectangle(R(3, 3, w - 3, d - 3), radius=4 * ppi,
                                   fill=(236, 242, 244, 255) if color else WHITE, outline=INK, width=1)
            draw.ellipse(R(5, d / 2 - 1, 7, d / 2 + 1), outline=INK)
    elif p.kind == "shower":
        box = R(0, 0, w, d)
        spec = tiles.get("SHOWER_TILE") or tiles.get("FLOOR_TILE")
        if color and spec:
            floor_spec = dict(spec, tile=(2, 2), pattern="stack", img=None)
            tex = tile_texture(floor_spec, (box[2] - box[0]) / ppi, (box[3] - box[1]) / ppi, ppi, seed=3)
            img.paste(tex, (round(box[0]), round(box[1])))
        else:
            draw.rectangle(box, fill=WHITE)
        draw.rectangle(box, outline=INK, width=lw)
        draw.rectangle(R(0.5, d - 4, w - 0.5, d), fill=COUNTER if color else WHITE, outline=INK, width=1)
        g = R(0, d - 2.3, w, d - 1.7)
        draw.rectangle(g, fill=GLASS_LINE if color else INK)
        c = R(w / 2 - 1.5, d / 2 - 1.5, w / 2 + 1.5, d / 2 + 1.5)
        draw.rectangle(c, outline=INK, width=1)
        if not color:
            a, b = R(0, 0, w, d - 4)[:2], R(0, 0, w, d - 4)[2:]
            draw.line([a[0], a[1], b[0], b[1]], fill=GRID, width=1)
            draw.line([a[0], b[1], b[0], a[1]], fill=GRID, width=1)


def plan_opening(img, draw, room, p, P, style, ppi, lw, colors):
    it = p.item
    t = WALL_THICKNESS
    u0, u1 = p.u - it.w / 2, p.u + it.w / 2

    def pt(u, b):
        return P(*room.to_plan(p.wall, u, b))

    a, b = pt(u0, -t), pt(u1, 0)
    box = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
    if p.kind == "window":
        draw.rectangle(box, fill=WHITE, outline=INK, width=1)
        for k in (0.35, 0.65):
            c1, c2 = pt(u0, -t * k), pt(u1, -t * k)
            draw.line([c1, c2], fill=INK, width=1)
        return
    draw.rectangle(box, fill=WHITE)
    for u in (u0, u1):                       # jambs
        draw.line([pt(u, -t), pt(u, 0)], fill=INK, width=lw)
    hinge_u = u0 if it.extra.get("swing", "right") == "left" else u1
    free_u = u1 if hinge_u == u0 else u0
    h = pt(hinge_u, 0)
    open_end = pt(hinge_u, it.w)
    draw.line([h, open_end], fill=INK, width=max(2, round(ppi * 0.9)))
    r = it.w * ppi
    a1 = math.degrees(math.atan2(open_end[1] - h[1], open_end[0] - h[0]))
    closed = pt(free_u, 0)
    a2 = math.degrees(math.atan2(closed[1] - h[1], closed[0] - h[0]))
    if (a2 - a1) % 360 > 180:
        a1, a2 = a2, a1
    draw.arc([h[0] - r, h[1] - r, h[0] + r, h[1] + r], a1, a2, fill=SOFT_INK, width=1)


def plan_dims(img, draw, room, placed, P, fnt, ppi):
    t = WALL_THICKNESS
    for wall in ("N", "E", "S", "W"):
        Lw = room.wall_len(wall)
        points = {0.0, Lw}
        for p in placed:
            if p.wall != wall or p.x is not None:
                continue
            if p.kind in ("vanity", "toilet", "tub", "shower", "door", "window"):
                points.add(round(p.u, 2))
            if p.kind == "vanity" and len(p.item.extra.get("sinks", [])) > 1:
                points.update(round(u, 2) for u in p.item.extra["sinks"])
        pts = sorted(points)
        tiers = [pts] if len(pts) == 2 else [pts, [0.0, Lw]]
        for tier_i, tier in enumerate(tiers):
            off = t + DIM_GAP_PX / ppi * (tier_i + 1)
            for a, b in zip(tier, tier[1:]):
                if b - a < 0.4:
                    continue
                pa = P(*room.to_plan(wall, a, -off))
                pb = P(*room.to_plan(wall, b, -off))
                ea = P(*room.to_plan(wall, a, -t))
                label = fmt_dim(b - a)
                if wall in ("N", "S"):
                    x1, x2 = sorted((pa[0], pb[0]))
                    hdim(img, draw, x1, x2, pa[1], label, fnt["small"], ext_from=ea[1])
                else:
                    y1, y2 = sorted((pa[1], pb[1]))
                    vdim(img, draw, pa[0], y1, y2, label, fnt["small"], ext_from=ea[0])


def north_arrow(draw, pos, fnt):
    x, y = pos
    s = 26
    draw.polygon([(x, y - s), (x - s * 0.45, y + s * 0.5), (x, y + s * 0.2), (x + s * 0.45, y + s * 0.5)],
                 fill=INK)
    draw.text((x, y - s - 4), "N", font=fnt["bold"], fill=INK, anchor="mb")

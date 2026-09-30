"""Renderings.

composite_render: a one-point-perspective view of the room built from the
    product photos (exact products, not photo-real). Free, always available.
ai_render: sends the composite, the colored plan, and the product photos to
    Google Gemini to get a photo-real image. Needs a Gemini API key.
"""

import base64
import io
import math

import numpy as np
import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from drawings import (COUNTER, GLASS, GLASS_LINE, PORCELAIN, draw_elevation, finish_color,
                      rgba, shade, tile_texture)
from products import fmt_dim

RENDER_SIZE = (2400, 1500)
EYE_HEIGHT = 60
CAMERA_BACK = 30          # camera stands this far behind the open (near) side


def perspective_coeffs(dst, src):
    """Coefficients for Image.transform(PERSPECTIVE): maps output points dst -> input points src."""
    rows = []
    for (x, y), (X, Y) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        rows.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
    return np.linalg.solve(np.array(rows, float), np.array(src, float).reshape(8))


class Camera:
    def __init__(self, back_len, depth, height, size):
        self.W, self.H = size
        self.xc, self.yc = back_len / 2, EYE_HEIGHT
        self.zc = depth + CAMERA_BACK
        self.f = min(0.60 * self.W / back_len, 0.64 * self.H / height) * self.zc

    def __call__(self, u, v, z):
        """u across the back wall from its left end, v up, z out from the back wall."""
        depth = self.zc - z
        return (self.W / 2 + self.f * (u - self.xc) / depth,
                self.H / 2 - self.f * (v - self.yc) / depth + self.H * 0.04)


def warp(canvas, tex, corners):
    """Paint texture onto the screen quad corners (TL, TR, BR, BL of the texture)."""
    tw, th = tex.size
    coeffs = perspective_coeffs(corners, [(0, 0), (tw, 0), (tw, th), (0, th)])
    out = tex.transform(canvas.size, Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    canvas.alpha_composite(out)


def darken(img, k):
    rgb = img.convert("RGB").point(lambda v: int(v * k))
    rgb.putalpha(img.getchannel("A"))
    return rgb


def composite_render(room, placed, tiles, colors, back_wall, fnt):
    W, H = RENDER_SIZE
    left_w, right_w = room.left_wall(back_wall), room.right_wall(back_wall)
    Lb, D, C = room.wall_len(back_wall), room.wall_len(left_w), room.ceiling
    cam = Camera(Lb, D, C, RENDER_SIZE)
    canvas = Image.new("RGBA", RENDER_SIZE, (235, 232, 226, 255))
    ppi = max(6, min(22, cam.f / cam.zc * 1.4))

    # ceiling
    ceil = Image.new("RGBA", (10, 10), rgba(shade(colors["wall"], 1.02)))
    warp(canvas, ceil, [cam(0, C, D), cam(Lb, C, D), cam(Lb, C, 0), cam(0, C, 0)])

    # floor, laid out in the back wall's frame
    ft = tiles.get("FLOOR_TILE")
    floor = tile_texture(ft, Lb, D, ppi, seed=7) if ft else Image.new("RGBA", (10, 10), (200, 192, 180, 255))
    warp(canvas, floor, [cam(0, 0, 0), cam(Lb, 0, 0), cam(Lb, 0, D), cam(0, 0, D)])

    # side walls: their flat elevations (tile, doors, windows, mirrors) in perspective
    side_ppi = min(ppi * 2, 4000 / max(D, 1))
    lt = draw_elevation(room, left_w, placed, tiles, colors, "color", side_ppi, dims=False, flat_only=True)
    warp(canvas, darken(lt, 0.86), [cam(0, C, D), cam(0, C, 0), cam(0, 0, 0), cam(0, 0, D)])
    rt = draw_elevation(room, right_w, placed, tiles, colors, "color", side_ppi, dims=False, flat_only=True)
    warp(canvas, darken(rt, 0.93), [cam(Lb, C, 0), cam(Lb, C, D), cam(Lb, 0, D), cam(Lb, 0, 0)])

    # back wall, full detail with product photos and shadows
    back = draw_elevation(room, back_wall, placed, tiles, colors, "color", ppi, dims=False, shadows=True)
    warp(canvas, back, [cam(0, C, 0), cam(Lb, C, 0), cam(Lb, 0, 0), cam(0, 0, 0)])

    # floor fixtures against the side walls, as simple 3D boxes, far to near
    boxes = []
    for p in placed:
        if p.kind in ("vanity", "toilet", "tub", "shower") and p.wall in (left_w, right_w) and p.x is None:
            x0, y0, x1, y1 = p.footprint(room)
            pts = [room.to_wall(back_wall, x, y) for x, y in ((x0, y0), (x1, y1))]
            u0, u1 = sorted(u for u, _ in pts)
            z0, z1 = sorted(z for _, z in pts)
            boxes.append((z0, p, u0, u1, z0, z1))
    for _, p, u0, u1, z0, z1 in sorted(boxes, key=lambda b: b[0]):
        draw_box_fixture(canvas, cam, p, u0, u1, z0, z1, Lb)

    # ceiling light
    for p in placed:
        if p.kind == "ceiling_light":
            u, z = room.to_wall(back_wall, p.x, p.y)
            r = p.item.w / 2
            ring = [cam(u + r * math.cos(a), C - 0.5, z + r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 40)]
            ImageDraw.Draw(canvas).polygon(ring, fill=(255, 250, 236, 255), outline=(200, 196, 188, 255))
            glow(canvas, cam(u, C - 2, z), cam.f / (cam.zc - z) * 30, 70)

    # lighting: corner shading, warm glow from wall lights, vignette
    ao = Image.new("L", RENDER_SIZE, 0)
    ad = ImageDraw.Draw(ao)
    for a, b in ((cam(0, 0, 0), cam(Lb, 0, 0)), (cam(0, C, 0), cam(Lb, C, 0)),
                 (cam(0, 0, 0), cam(0, C, 0)), (cam(Lb, 0, 0), cam(Lb, C, 0)),
                 (cam(0, 0, 0), cam(0, 0, D)), (cam(Lb, 0, 0), cam(Lb, 0, D))):
        ad.line([a, b], fill=120, width=int(ppi * 3))
    ao = ao.filter(ImageFilter.GaussianBlur(ppi * 3))
    shadow_layer = Image.new("RGBA", RENDER_SIZE, (25, 20, 15, 255))
    shadow_layer.putalpha(ao)
    canvas.alpha_composite(shadow_layer)

    for p in placed:
        if p.wall == back_wall and p.kind in ("sconce", "vanity_light") and p.x is None:
            glow(canvas, cam(p.u, p.bottom + p.item.h * 0.6, 0), cam.f / cam.zc * 22, 85)

    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot((xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2))
    vignette = np.clip(1.06 - 0.22 * r ** 2, 0.7, 1.0)
    rgb = np.asarray(canvas.convert("RGB"), dtype=float) * vignette[..., None]
    rgb *= np.array([1.02, 1.0, 0.97])                  # slightly warm
    out = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    return out


def glow(canvas, center, radius, strength):
    W, H = canvas.size
    layer = Image.new("L", canvas.size, 0)
    d = ImageDraw.Draw(layer)
    cx, cy = center
    for i in range(12, 0, -1):
        rr = radius * i / 12
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=int(strength * (1 - i / 13)))
    layer = layer.filter(ImageFilter.GaussianBlur(radius / 6))
    warm = Image.new("RGB", canvas.size, (255, 214, 150))
    base = canvas.convert("RGB")
    screened = ImageChops.screen(base, warm)
    mixed = Image.composite(screened, base, layer)
    mixed.putalpha(canvas.getchannel("A"))
    canvas.paste(mixed)


def draw_box_fixture(canvas, cam, p, u0, u1, z0, z1, Lb):
    """Simple shaded 3D block for a floor fixture on a side wall."""
    d = ImageDraw.Draw(canvas)
    it = p.item
    on_left = u0 < Lb / 2
    inner_u = u1 if on_left else u0            # face toward the room center
    h = it.h
    if p.kind == "shower":
        curb = 4
        top = [cam(u0, curb, z0), cam(u1, curb, z0), cam(u1, curb, z1), cam(u0, curb, z1)]
        d.polygon(top, fill=COUNTER)
        glass = [cam(inner_u, curb, z0), cam(inner_u, h, z0), cam(inner_u, h, z1), cam(inner_u, curb, z1)]
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon(glass, fill=GLASS, outline=GLASS_LINE)
        canvas.alpha_composite(layer)
        return
    base = PORCELAIN if p.kind in ("toilet", "tub") else rgba(it.color or finish_color(it.finish))
    if p.kind == "tub" and it.extra.get("type") == "freestanding":
        draw_oval_tub(d, cam, u0, u1, z0, z1, h)
        return
    if p.kind == "toilet":
        wall_u = u0 if on_left else u1
        tank_u = wall_u + (9 if on_left else -9)
        tz0, tz1 = (z0 + z1) / 2 - 10, (z0 + z1) / 2 + 10
        block(d, cam, sorted((wall_u, tank_u)), (15, h), (tz0, tz1), base, on_left)
        bz0, bz1 = (z0 + z1) / 2 - 7.5, (z0 + z1) / 2 + 7.5
        block(d, cam, sorted((wall_u + (6 if on_left else -6), inner_u)), (0, 16), (bz0, bz1), base, on_left)
        return
    block(d, cam, (u0, u1), (0, h), (z0, z1), base, on_left)
    if p.kind == "tub":
        inset = 3
        basin = [cam(u0 + inset, h, z0 + inset), cam(u1 - inset, h, z0 + inset),
                 cam(u1 - inset, h, z1 - inset), cam(u0 + inset, h, z1 - inset)]
        d.polygon(basin, fill=(228, 234, 236, 255))
    if p.kind == "vanity":
        top = [cam(u0, h, z0), cam(u1 + (0.5 if on_left else 0), h, z0),
               cam(u1, h, z1 + 0.5), cam(u0, h, z1 + 0.5)]
        d.polygon(top, fill=COUNTER)


def draw_oval_tub(d, cam, u0, u1, z0, z1, h):
    """Freestanding tub: a rounded tub body with a lighter rim and basin."""
    cu, cz = (u0 + u1) / 2, (z0 + z1) / 2
    ru, rz = (u1 - u0) / 2, (z1 - z0) / 2
    angles = np.linspace(0, 2 * math.pi, 60, endpoint=False)

    def ring(v, k):                          # rounded-rectangle-ish oval (superellipse)
        pts = []
        for a in angles:
            c, s = math.cos(a), math.sin(a)
            pts.append(cam(cu + ru * k * np.sign(c) * abs(c) ** 0.6, v, cz + rz * k * np.sign(s) * abs(s) ** 0.6))
        return pts

    body = convex_hull(ring(h, 1.0) + ring(0, 0.9))
    d.polygon(body, fill=shade(PORCELAIN, 0.9), outline=shade(PORCELAIN, 0.7))
    d.polygon(ring(h, 1.0), fill=PORCELAIN, outline=shade(PORCELAIN, 0.75))
    d.polygon(ring(h, 0.86), fill=(226, 233, 236, 255), outline=shade(PORCELAIN, 0.8))


def convex_hull(points):
    pts = sorted(set((round(x, 1), round(y, 1)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def block(d, cam, us, vs, zs, color, on_left):
    (u0, u1), (v0, v1), (z0, z1) = us, vs, zs
    inner_u = u1 if on_left else u0
    d.polygon([cam(inner_u, v0, z0), cam(inner_u, v1, z0), cam(inner_u, v1, z1), cam(inner_u, v0, z1)],
              fill=shade(color, 0.92), outline=shade(color, 0.7))
    d.polygon([cam(u0, v1, z1), cam(u1, v1, z1), cam(u1, v0, z1), cam(u0, v0, z1)],
              fill=shade(color, 0.8), outline=shade(color, 0.6))
    d.polygon([cam(u0, v1, z0), cam(u1, v1, z0), cam(u1, v1, z1), cam(u0, v1, z1)],
              fill=shade(color, 1.0), outline=shade(color, 0.7))


# ---------------------------------------------------------------- Gemini

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def png_b64(img, max_side=1024):
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def gemini_image(prompt, images, key, model, aspect="16:9"):
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/png", "data": png_b64(i)}}
                                  for i in images]
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": aspect}}}
    resp = requests.post(GEMINI_URL.format(model=model), json=body, timeout=300,
                         headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini returned {resp.status_code}: {resp.text[:300]}")
    for cand in resp.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            data = part.get("inlineData") or part.get("inline_data")
            if data and data.get("data"):
                return Image.open(io.BytesIO(base64.b64decode(data["data"]))).convert("RGB")
    raise RuntimeError("Gemini answered without an image (it may have declined the request).")


def describe_room(room, placed, tiles, colors):
    lines = [f"Room: {fmt_dim(room.width)} wide x {fmt_dim(room.length)} long, "
             f"{fmt_dim(room.ceiling)} ceiling. Wall paint color {'#%02X%02X%02X' % colors['wall']}."]
    seen = set()
    for p in placed:
        it = p.item
        if it.section in seen:
            continue
        seen.add(it.section)
        count = sum(1 for q in placed if q.item is it)
        what = it.name or it.label
        finish = f", finish {it.finish}" if it.finish else ""
        qty = f"{count} x " if count > 1 else ""
        lines.append(f"- {it.label}: {qty}{what}{finish}, {fmt_dim(it.w)} W x {fmt_dim(it.h)} H.")
    for key, spec in tiles.items():
        if spec:
            name = spec.get("finish") or "tile"
            lines.append(f"- {key.replace('_', ' ').title()}: {name}, {spec['tile'][0]:g}x{spec['tile'][1]:g} in, "
                         f"{spec.get('pattern', 'offset')} pattern, color {'#%02X%02X%02X' % spec['color']}.")
    return "\n".join(lines)


def ai_renders(room, placed, tiles, colors, composite, plan_color, key, model):
    """Returns [(title, image)] - eye-level photo and bird's-eye cutaway."""
    photos = []
    for p in placed:
        if p.item.img is not None and all(p.item.img is not q for q in photos):
            photos.append(p.item.img)
    refs = []
    for ph in photos[:6]:
        white = Image.new("RGB", ph.size, (255, 255, 255))
        white.paste(ph, mask=ph.getchannel("A"))
        refs.append(white)
    spec = describe_room(room, placed, tiles, colors)
    common = ("This is a residential bathroom design for a luxury Los Angeles home. "
              "Keep every fixture exactly where the layout shows it, with the same size and proportions. "
              "Where product photos are provided, the fixtures must look exactly like those products "
              "(same shape, color, and finish). Do not add fixtures that are not listed. "
              "No people, no text, no watermarks.\n\n" + spec)
    jobs = [
        ("RENDERING - EYE LEVEL (AI)",
         "Create a photorealistic interior photograph of this bathroom. Image 1 is a rough composite "
         "showing the exact camera angle and layout; match its composition. Image 2 is the floor plan. "
         "The remaining images are the actual products. Architectural photography, 24mm lens, soft "
         "natural daylight plus warm light from the fixtures, high-end finishes, realistic reflections "
         "and shadows.\n\n" + common, "16:9"),
        ("BIRD'S-EYE VIEW (AI)",
         "Create a photorealistic 3D cutaway bird's-eye view of this bathroom, seen from above at a "
         "45-degree angle, ceiling removed and walls cut at about 7 feet so the whole room is visible. "
         "Image 2 is the floor plan: match the room shape and every fixture position exactly. Image 1 "
         "shows the materials and products from eye level. The remaining images are the actual products. "
         "Clean, bright, architectural-visualization style.\n\n" + common, "4:3"),
    ]
    results = []
    for title, prompt, aspect in jobs:
        print(f"  Asking Gemini for: {title.lower()} ...")
        results.append((title, gemini_image(prompt, [composite, plan_color, *refs], key, model, aspect)))
    return results

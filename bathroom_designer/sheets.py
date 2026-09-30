"""11x17 PDF sheet set with a title block on every page."""

import datetime as dt
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw

from drawings import INK, SOFT_INK, load_font
from products import fmt_dim

DPI = 200
SHEET_IN = (17, 11)
MARGIN_IN = 0.35
TITLE_W_IN = 2.5
# (paper inches per real foot, label), largest first
SCALES = [(1.5, '1 1/2" = 1\'-0"'), (1.0, '1" = 1\'-0"'), (0.75, '3/4" = 1\'-0"'), (0.5, '1/2" = 1\'-0"'),
          (0.375, '3/8" = 1\'-0"'), (0.25, '1/4" = 1\'-0"'), (0.1875, '3/16" = 1\'-0"'),
          (0.125, '1/8" = 1\'-0"')]
LABEL_H_IN = 0.45


def px(inches):
    return round(inches * DPI)


def drawing_area():
    """Usable area left of the title block, in inches (x0, y0, x1, y1)."""
    return (MARGIN_IN + 0.15, MARGIN_IN + 0.15, SHEET_IN[0] - MARGIN_IN - TITLE_W_IN - 0.15,
            SHEET_IN[1] - MARGIN_IN - 0.15)


def split(area, n, gap=0.3):
    x0, y0, x1, y1 = area
    w = (x1 - x0 - gap * (n - 1)) / n
    return [(x0 + i * (w + gap), y0, x0 + i * (w + gap) + w, y1) for i in range(n)]


def pick_scale(extent_fns, slot):
    """Largest standard scale where every drawing fits in the slot.
    extent_fns: functions ppi -> (width, height) in real inches (margins depend on scale)."""
    sw, sh = slot[2] - slot[0], slot[3] - slot[1] - LABEL_H_IN
    for per_foot, label in SCALES:
        k = per_foot / 12
        if all(w * k <= sw and h * k <= sh for w, h in (fn(k * DPI) for fn in extent_fns)):
            return k * DPI, label
    k = SCALES[-1][0] / 12
    return k * DPI, "NOT TO SCALE (reduced to fit)"


class SheetSet:
    def __init__(self, meta, logo_path):
        self.meta = meta
        self.pages = []
        self.logo = None
        if logo_path and Path(logo_path).is_file():
            try:
                self.logo = Image.open(logo_path).convert("RGBA")
            except OSError:
                print(f"  Could not open logo {logo_path}; using the company name instead.")
        self.f = {k: load_font(v, b) for k, (v, b) in {
            "tiny": (22, False), "small": (26, False), "normal": (30, False), "bold": (30, True),
            "title": (40, True), "label": (32, True)}.items()}

    def new_page(self, title):
        img = Image.new("RGB", (px(SHEET_IN[0]), px(SHEET_IN[1])), "white")
        self.pages.append((img, title))
        d = ImageDraw.Draw(img)
        d.rectangle([px(MARGIN_IN), px(MARGIN_IN), px(SHEET_IN[0] - MARGIN_IN), px(SHEET_IN[1] - MARGIN_IN)],
                    outline=INK, width=4)
        return img

    def place(self, page, drawing, slot, number, title, scale_label, fit=False):
        """Center a drawing in slot (inches) with its label underneath. fit=True resizes it to fit."""
        x0, y0, x1, y1 = slot
        box_w, box_h = px(x1 - x0), px(y1 - y0 - LABEL_H_IN)
        if fit or drawing.width > box_w or drawing.height > box_h:
            k = min(box_w / drawing.width, box_h / drawing.height)
            drawing = drawing.resize((max(1, round(drawing.width * k)), max(1, round(drawing.height * k))),
                                     Image.LANCZOS)
        left = px(x0) + (box_w - drawing.width) // 2
        top = px(y0) + (box_h - drawing.height) // 2
        page.paste(drawing.convert("RGB"), (left, top))
        d = ImageDraw.Draw(page)
        ly = px(y1 - LABEL_H_IN) + 12
        cx = left + 30
        d.ellipse([cx - 26, ly, cx + 26, ly + 52], outline=INK, width=3)
        d.text((cx, ly + 26), str(number), font=self.f["bold"], fill=INK, anchor="mm")
        d.text((cx + 44, ly + 4), title, font=self.f["label"], fill=INK)
        d.line([cx + 44, ly + 44, cx + 44 + max(360, self.f["label"].getbbox(title)[2] + 10), ly + 44],
               fill=INK, width=3)
        d.text((cx + 44, ly + 50), scale_label, font=self.f["tiny"], fill=SOFT_INK)

    def title_block(self, page, sheet_title, number, total):
        d = ImageDraw.Draw(page)
        x0 = px(SHEET_IN[0] - MARGIN_IN - TITLE_W_IN)
        x1 = px(SHEET_IN[0] - MARGIN_IN)
        y0, y1 = px(MARGIN_IN), px(SHEET_IN[1] - MARGIN_IN)
        d.line([x0, y0, x0, y1], fill=INK, width=4)
        pad = 34
        y = y0 + pad
        if self.logo:
            logo = self.logo.copy()
            logo.thumbnail((px(TITLE_W_IN) - 2 * pad, px(0.7)))
            page.paste(logo, (x0 + pad, y), logo)
            y += logo.height + 18
        for line in wrap(self.meta["company"], self.f["bold"], px(TITLE_W_IN) - 2 * pad):
            d.text((x0 + pad, y), line, font=self.f["bold"], fill=INK)
            y += 38
        y += 24
        d.line([x0, y, x1, y], fill=INK, width=2)
        y += 30

        def field(label, value, font="normal"):
            nonlocal y
            if not value:
                return
            d.text((x0 + pad, y), label.upper(), font=self.f["tiny"], fill=SOFT_INK)
            y += 30
            for line in wrap(value, self.f[font], px(TITLE_W_IN) - 2 * pad):
                d.text((x0 + pad, y), line, font=self.f[font], fill=INK)
                y += 38
            y += 22

        field("Client", self.meta.get("client"), "bold")
        field("Project", self.meta.get("project"))
        field("Room", self.meta.get("room"))

        # sheet info at the bottom
        yb = y1 - 330
        d.line([x0, yb, x1, yb], fill=INK, width=2)
        d.text((x0 + pad, yb + 26), "SHEET", font=self.f["tiny"], fill=SOFT_INK)
        ty = yb + 56
        for line in wrap(sheet_title, self.f["bold"], px(TITLE_W_IN) - 2 * pad):
            d.text((x0 + pad, ty), line, font=self.f["bold"], fill=INK)
            ty += 38
        d.text((x0 + pad, y1 - 170), "DATE", font=self.f["tiny"], fill=SOFT_INK)
        d.text((x0 + pad, y1 - 140), self.meta["date"], font=self.f["small"], fill=INK)
        d.line([x0, y1 - 96, x1, y1 - 96], fill=INK, width=2)
        d.text(((x0 + x1) / 2, y1 - 48), f"A-{number}", font=self.f["title"], fill=INK, anchor="mm")
        d.text((x1 - 16, y1 - 12), f"{number} of {total}", font=self.f["tiny"], fill=SOFT_INK, anchor="rb")

    def schedule(self, items_placed, notes):
        page = self.new_page("FIXTURE SCHEDULE & NOTES")
        d = ImageDraw.Draw(page)
        x0, y0, x1, y1 = drawing_area()
        cols = [("", 0.9), ("ITEM", 1.5), ("PRODUCT", 4.2), ("FINISH", 1.9), ("SIZE (W x D x H)", 2.4),
                ("QTY", 0.6), ("SOURCE", 1.8)]
        X = [px(x0)]
        for _, w in cols:
            X.append(X[-1] + px(w))
        y = px(y0) + 10
        d.text((X[0], y), "FIXTURE SCHEDULE", font=self.f["title"], fill=INK)
        y += 80
        for (name, _), x in zip(cols, X):
            d.text((x + 8, y), name, font=self.f["tiny"], fill=SOFT_INK)
        y += 36
        d.line([X[0], y, X[-1], y], fill=INK, width=3)
        row_h = px(0.62)
        for it, qty in items_placed:
            if y + row_h > px(y1) - px(2.4):
                break
            cy = y + row_h // 2
            if it.img is not None:
                thumb = Image.new("RGB", it.img.size, "white")
                thumb.paste(it.img, mask=it.img.getchannel("A"))
                thumb.thumbnail((px(0.8), row_h - 12))
                page.paste(thumb, (X[0] + 6, y + (row_h - thumb.height) // 2))
            size = f"{fmt_dim(it.w)} x {fmt_dim(it.d)} x {fmt_dim(it.h)}"
            source = urlparse(it.url).netloc.replace("www.", "") if it.url else "not selected"
            values = [it.label, it.name or "-", it.finish or "-", size, str(qty), source]
            for v, x, (_, w) in zip(values, X[1:], cols[1:]):
                lines = wrap(v, self.f["small"], px(w) - 16)[:2]
                for i, line in enumerate(lines):
                    d.text((x + 8, cy - 16 * len(lines) + 34 * i), line, font=self.f["small"], fill=INK)
            y += row_h
            d.line([X[0], y, X[-1], y], fill=(200, 196, 190), width=1)

        ny = px(y1) - px(2.2)
        d.text((X[0], ny), "NOTES", font=self.f["title"], fill=INK)
        ny += 64
        general = ["Verify all dimensions and rough-in locations in the field before ordering or framing.",
                   "Drawing scales are correct only when printed on 11x17 paper at 100%.",
                   "Renderings are for design intent; actual products govern."]
        for note in [f"CHECK: {n}" for n in notes] + general:
            for line in wrap("- " + note, self.f["small"], px(x1 - x0)):
                d.text((X[0], ny), line, font=self.f["small"], fill=(170, 40, 30) if note.startswith("CHECK") else INK)
                ny += 36
        return page

    def save(self, path):
        total = len(self.pages)
        for i, (page, title) in enumerate(self.pages, 1):
            self.title_block(page, title, i, total)
        imgs = [p for p, _ in self.pages]
        imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=DPI, quality=92)


def wrap(text, font, width):
    words, lines, line = str(text).split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if font.getbbox(trial)[2] <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines or [""]


def today():
    return dt.date.today().strftime("%b %d, %Y")

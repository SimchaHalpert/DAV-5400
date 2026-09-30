"""Bathroom designer: product links -> PDF sheet set.

    python build.py --templates                    list the starting layouts
    python build.py --new primary_bath_10x12       make project.txt from a layout
    python build.py                                build the PDF from project.txt
    python build.py my_project.txt --no-ai         other file, skip the AI renders
"""

import argparse
import os
import re
import sys
from pathlib import Path

from drawings import (draw_elevation, draw_plan, elevation_extent, finish_color, fonts,
                      parse_color, plan_extent)
from products import dominant_color, get_product, real_size, remove_background, to_inches
from project import (FIXTURES, FREESTANDING_TUB, TEMPLATES, TILES, WALL_NAMES, WALLS, Item, Room,
                     check_layout, included, parse_tile_size, place_items, read_project, template_size,
                     write_project)
from render import ai_renders, composite_render
from sheets import SheetSet, drawing_area, pick_scale, split, today

HERE = Path(__file__).resolve().parent


def load_items(cfg):
    items = {}
    template = cfg.get("PROJECT", {}).get("TEMPLATE", "")
    for section, (kind, dw, dh, dd) in FIXTURES.items():
        entry = cfg.get(section)
        if not included(entry):
            continue
        default = (dw, dh, dd)
        extra = {}
        if section == "TUB":
            extra["type"] = (entry.get("TYPE") or "alcove").lower()
            if extra["type"] == "freestanding":
                default = FREESTANDING_TUB
        t = template_size(template, section)
        default = (t.get("WIDTH", default[0]), t.get("HEIGHT", default[1]), t.get("DEPTH", default[2]))
        if section == "DOOR":
            extra["swing"] = (entry.get("SWING") or "right").lower()
        url, finish = entry.get("URL", ""), entry.get("FINISH", "")
        img, scraped, name = None, {}, ""
        if url:
            try:
                raw, scraped, name = get_product(section, url, finish, kind)
                img = remove_background(raw)
            except Exception as exc:
                print(f"  {section}: FAILED to read {url} ({exc}). Drawing a generic {kind} instead. "
                      "If the site blocks bots, paste the photo's image address instead.")
        w, h, d = real_size(section, kind, entry, scraped, img, default)
        color = dominant_color(img) if img is not None else None
        if finish and img is None:
            color = finish_color(finish, None)
        items[section] = Item(section, kind, w, h, d, finish=finish, url=url, name=name, img=img,
                              color=color, extra=extra)
        src = "project file" if entry.get("HEIGHT") else ("website" if scraped else
                                                          "photo" if img is not None else "standard size")
        print(f"  {section:<13} {w:5.1f} W x {d:5.1f} D x {h:5.1f} H  ({src})")
    return items


def load_tiles(cfg):
    tiles = {}
    for section, default_size in TILES.items():
        e = cfg.get(section)
        if not e:
            continue
        img = None
        if e.get("URL"):
            try:
                img, _, _ = get_product(section, e["URL"], e.get("FINISH", ""), "tile", prefer_studio=False)
            except Exception as exc:
                print(f"  {section}: FAILED to read {e['URL']} ({exc}). Using COLOR instead.")
        color = parse_color(e.get("COLOR"), None)
        if img is not None and not e.get("COLOR"):
            w, h = img.size
            color = dominant_color(img.convert("RGBA").crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4)))
        color = color or finish_color(e.get("FINISH"), (225, 222, 216))
        height = e.get("HEIGHT", "0").strip().lower()
        tiles[section] = {
            "img": img, "color": color, "finish": e.get("FINISH", ""),
            "tile": parse_tile_size(e.get("TILE_SIZE"), default_size),
            "pattern": e.get("PATTERN") or "offset",
            "grout": parse_color(e.get("GROUT"), (220, 216, 210)),
            "height": None if height in ("full", "ceiling") else (to_inches(height) or 0),
        }
    return tiles


def main():
    ap = argparse.ArgumentParser(description="Bathroom designer: product links -> PDF sheet set")
    ap.add_argument("project", nargs="?", default="project.txt")
    ap.add_argument("--new", metavar="TEMPLATE", help="create the project file from a starting layout")
    ap.add_argument("--templates", action="store_true", help="list starting layouts")
    ap.add_argument("--no-ai", action="store_true", help="skip the Gemini AI renderings")
    ap.add_argument("--out", default=str(HERE / "output"))
    args = ap.parse_args()

    if args.templates:
        for name, t in TEMPLATES.items():
            print(f"  {name:<22} {t['about']}")
        return
    path = Path(args.project)
    if not path.is_absolute() and not path.exists():
        path = HERE / path
    if args.new:
        if path.exists():
            sys.exit(f"{path} already exists. Rename or delete it first, or pick another name.")
        write_project(path, args.new)
        print(f"Created {path}. Fill in the product links, then run: python build.py {path.name}")
        return
    if not path.exists():
        sys.exit(f"No {path.name}. Start one with:  python build.py --new primary_bath_10x12\n"
                 f"Layouts:  python build.py --templates")

    cfg = read_project(path)
    room_cfg, proj, settings = cfg.get("ROOM", {}), cfg.get("PROJECT", {}), cfg.get("SETTINGS", {})
    room = Room(to_inches(room_cfg.get("WIDTH")) or 60, to_inches(room_cfg.get("LENGTH")) or 96,
                to_inches(room_cfg.get("CEILING")) or 96)
    colors = {"wall": parse_color(room_cfg.get("WALL_COLOR"), (242, 239, 234)),
              "trim": parse_color(room_cfg.get("TRIM_COLOR"), (255, 255, 255))}

    print("Reading products...")
    items = load_items(cfg)
    tiles = load_tiles(cfg)
    placed = place_items(cfg, items, room, settings)
    notes = check_layout(room, placed)
    for n in notes:
        print(f"  CHECK: {n}")

    vanity = next((p for p in placed if p.kind == "vanity"), None)
    back_wall = (settings.get("RENDER_WALL") or (vanity.wall if vanity else "N")).upper()[:1]
    if back_wall not in WALLS:
        back_wall = "N"
    walls_in_use = [w for w in WALLS if any(p.wall == w and p.x is None for p in placed)] or WALLS

    print("Drawing...")
    fnt = fonts(26)
    composite = composite_render(room, placed, tiles, colors, back_wall, fnt)
    meta = {"company": proj.get("COMPANY") or "Aggregate Construction Group", "client": proj.get("CLIENT"),
            "project": proj.get("PROJECT"), "room": proj.get("ROOM_NAME"), "date": today()}
    logo = proj.get("LOGO")
    if logo and not Path(logo).is_absolute():
        logo = str(path.parent / logo)
    sheets = SheetSet(meta, logo)
    area = drawing_area()

    page = sheets.new_page("RENDERING")
    sheets.place(page, composite, area, 1, f"RENDERING - {WALL_NAMES[back_wall].upper()} WALL",
                 "Built from the selected product photos. Not to scale.", fit=True)

    ai_images = []
    key = settings.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    plan_ppi, plan_scale = pick_scale([lambda ppi: plan_extent(room, ppi)], split(area, 2)[0])
    plan_line = draw_plan(room, placed, tiles, colors, "line", plan_ppi, fnt)
    plan_color = draw_plan(room, placed, tiles, colors, "color", plan_ppi, fnt)
    if args.no_ai:
        print("  AI renderings skipped (--no-ai).")
    elif not key:
        print("  AI renderings skipped: no GEMINI_API_KEY (see README).")
    else:
        try:
            ai_images = ai_renders(room, placed, tiles, colors, composite, plan_color, key,
                                   settings.get("GEMINI_MODEL") or "gemini-2.5-flash-image")
        except Exception as exc:
            print(f"  AI renderings FAILED: {exc}")
    for title, img in ai_images:
        page = sheets.new_page(title)
        sheets.place(page, img, area, 1, title, "AI-generated from the design. For design intent only.", fit=True)

    page = sheets.new_page("FLOOR PLAN")
    left, right = split(area, 2)
    sheets.place(page, plan_color, left, 1, "FLOOR PLAN - BIRD'S-EYE (COLOR)", plan_scale)
    sheets.place(page, plan_line, right, 2, "FLOOR PLAN - DIMENSIONED", plan_scale)

    for wall in WALLS:
        if wall not in walls_in_use and not any(p.kind in ("door", "window") and p.wall == wall for p in placed):
            continue
        slot_l, slot_r = split(area, 2)
        ppi, scale = pick_scale([lambda ppi, w=wall: elevation_extent(room, w, ppi)], slot_l)
        name = f"{WALL_NAMES[wall].upper()} WALL"
        page = sheets.new_page(f"ELEVATION - {name}")
        sheets.place(page, draw_elevation(room, wall, placed, tiles, colors, "color", ppi, fnt), slot_l,
                     1, f"{name} ELEVATION (COLOR)", scale)
        sheets.place(page, draw_elevation(room, wall, placed, tiles, colors, "line", ppi, fnt), slot_r,
                     2, f"{name} ELEVATION - DIMENSIONED", scale)

    counts = {}
    for p in placed:
        counts[p.item.section] = counts.get(p.item.section, 0) + 1
    sheets.schedule([(items[s], n) for s, n in counts.items()], notes)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{meta['client'] or ''} {meta['room'] or 'bathroom'}").strip("_")
    pdf = out_dir / f"{stem}.pdf"
    sheets.save(pdf)
    composite.save(out_dir / f"{stem}_rendering.png")
    for i, (_, img) in enumerate(ai_images, 1):
        img.save(out_dir / f"{stem}_ai_{i}.png")
    print(f"\nSaved {pdf}  ({len(sheets.pages)} sheets)")


if __name__ == "__main__":
    main()

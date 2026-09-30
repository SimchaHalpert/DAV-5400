"""Project file (templates, reading, writing) and room geometry.

Coordinates, in inches:
  Plan: x runs west -> east (0..WIDTH), y runs north -> south (0..LENGTH).
  North wall is at the top of the floor plan.
  A fixture's POSITION is the distance from the LEFT corner of its wall, as
  you stand in the room facing that wall, to the fixture's CENTER.
"""

import re
from dataclasses import dataclass, field

from products import to_inches

WALLS = ["N", "E", "S", "W"]
WALL_NAMES = {"N": "North", "E": "East", "S": "South", "W": "West"}
WALL_THICKNESS = 4.5

# Section -> (kind, default W, H, D). Order = order in the project file.
FIXTURES = {
    "VANITY":        ("vanity", 36, 34.5, 21.5),
    "FAUCET":        ("faucet", 6, 8, 5),
    "MIRROR":        ("mirror", 24, 32, 1),
    "SCONCES":       ("sconce", 5, 12, 5),
    "VANITY_LIGHT":  ("vanity_light", 24, 8, 6),
    "TOILET":        ("toilet", 19, 30, 28),
    "TUB":           ("tub", 60, 20, 30),
    "SHOWER":        ("shower", 60, 76, 36),
    "SHOWER_TRIM":   ("shower_trim", 10, 38, 6),
    "CEILING_LIGHT": ("ceiling_light", 14, 8, 14),
    "DOOR":          ("door", 30, 80, 2),
    "WINDOW":        ("window", 30, 36, 4),
}
TILES = {"WALL_TILE": "12x3", "SHOWER_TILE": "12x3", "FLOOR_TILE": "24x12"}
FREESTANDING_TUB = (66, 23, 32)

SECTION_HELP = {
    "PROJECT": "Shows in the title block of every sheet. LOGO = path to your logo image (png/jpg).",
    "ROOM": "Room size in inches. WIDTH = west-east, LENGTH = north-south.\n"
            "WALL_COLOR / TRIM_COLOR: hex code (#F2EFEA) or a name (white).",
    "VANITY": "SINKS = 1 or 2.",
    "FAUCET": "Sits on the vanity, one per sink. WALL/POSITION come from the vanity.",
    "MIRROR": "Above the vanity. COUNT = number of mirrors (blank = one per sink).\n"
              "OFF_FLOOR blank = MIRROR_GAP inches above the faucet (never below 40\").",
    "SCONCES": "Flank each mirror. OFF_FLOOR blank = centered at 66\".",
    "VANITY_LIGHT": "Above each mirror.",
    "TOILET": "",
    "TUB": "TYPE = alcove or freestanding. OFF_WALL = gap to the wall (freestanding).",
    "SHOWER": "WIDTH along the wall, DEPTH into the room. HEIGHT = glass height.",
    "SHOWER_TRIM": "Valve + shower head. OFF_FLOOR = height of the bottom of the valve.",
    "CEILING_LIGHT": "X / Y = inches from the north-west corner.",
    "DOOR": "SWING = left or right (hinge side, standing in the room facing the door).",
    "WINDOW": "OFF_FLOOR = sill height.",
    "WALL_TILE": "HEIGHT = tile height on the walls (0 = paint only, full = to ceiling).\n"
                 "TILE_SIZE = width x height as installed (12x3 = horizontal subway). PATTERN = stack or offset. GROUT = color.",
    "SHOWER_TILE": "Tile inside the shower / tub surround. HEIGHT: inches or full.",
    "FLOOR_TILE": "",
    "SETTINGS": "RENDER_WALL = which wall the rendering looks at (blank = the vanity wall).\n"
                "GEMINI_API_KEY = key for the AI renderings (or set the GEMINI_API_KEY\n"
                "environment variable). Blank = skip the AI sheets.",
}

FIXTURE_FIELDS = ["INCLUDE", "URL", "FINISH", "WIDTH", "HEIGHT", "DEPTH", "WALL", "POSITION", "OFF_FLOOR"]
EXTRA_FIELDS = {
    "VANITY": ["SINKS"],
    "MIRROR": ["COUNT"],
    "TUB": ["TYPE", "OFF_WALL"],
    "DOOR": ["SWING"],
    "CEILING_LIGHT": ["X", "Y"],
}
NO_POSITION = {"FAUCET", "MIRROR", "SCONCES", "VANITY_LIGHT", "CEILING_LIGHT"}
TILE_FIELDS = ["URL", "FINISH", "COLOR", "TILE_SIZE", "HEIGHT", "PATTERN", "GROUT"]
PROJECT_FIELDS = ["COMPANY", "LOGO", "CLIENT", "PROJECT", "ROOM_NAME", "TEMPLATE"]
SIZE_FIELDS = ("WIDTH", "HEIGHT", "DEPTH")
FIXED_SIZE = {"DOOR", "WINDOW"}          # sizes here are the opening, not a product
ROOM_FIELDS = ["WIDTH", "LENGTH", "CEILING", "WALL_COLOR", "TRIM_COLOR"]
SETTINGS_FIELDS = ["MIRROR_GAP", "RENDER_WALL", "GEMINI_API_KEY", "GEMINI_MODEL"]

COMMON = {
    "PROJECT": {"COMPANY": "Aggregate Construction Group", "LOGO": "logo.png",
                "CLIENT": "", "PROJECT": "Bathroom remodel", "ROOM_NAME": ""},
    "ROOM": {"CEILING": "96", "WALL_COLOR": "#F2EFEA", "TRIM_COLOR": "#FFFFFF"},
    "FAUCET": {"INCLUDE": "yes"},
    "MIRROR": {"INCLUDE": "yes"},
    "SCONCES": {"INCLUDE": "yes"},
    "VANITY_LIGHT": {"INCLUDE": "no"},
    "CEILING_LIGHT": {"INCLUDE": "yes"},
    "WALL_TILE": {"HEIGHT": "0", "TILE_SIZE": "12x3", "PATTERN": "offset", "GROUT": "#E6E3DE",
                  "COLOR": "#F4F2EE"},
    "SHOWER_TILE": {"HEIGHT": "full", "TILE_SIZE": "12x3", "PATTERN": "offset", "GROUT": "#E6E3DE",
                    "COLOR": "#EDEBE7"},
    "FLOOR_TILE": {"TILE_SIZE": "24x12", "PATTERN": "offset", "GROUT": "#BDB6AC", "COLOR": "#CFC8BD"},
    "SETTINGS": {"MIRROR_GAP": "6", "RENDER_WALL": "", "GEMINI_API_KEY": "",
                 "GEMINI_MODEL": "gemini-2.5-flash-image"},
}

TEMPLATES = {
    "hall_bath_5x8": {
        "about": "5' x 8' hall bath: tub/shower combo across the end, vanity + toilet on one long wall.",
        "PROJECT": {"ROOM_NAME": "Hall Bath"},
        "ROOM": {"WIDTH": "60", "LENGTH": "96"},
        "VANITY": {"INCLUDE": "yes", "WIDTH": "30", "WALL": "W", "POSITION": "76", "SINKS": "1"},
        "TOILET": {"INCLUDE": "yes", "WALL": "W", "POSITION": "46"},
        "TUB": {"INCLUDE": "yes", "TYPE": "alcove", "WALL": "S", "POSITION": "30", "WIDTH": "60"},
        "SHOWER": {"INCLUDE": "no"},
        "SHOWER_TRIM": {"INCLUDE": "yes", "WALL": "W", "POSITION": "15", "OFF_FLOOR": "30"},
        "DOOR": {"INCLUDE": "yes", "WALL": "E", "POSITION": "24", "SWING": "right"},
        "WINDOW": {"INCLUDE": "no"},
        "CEILING_LIGHT": {"X": "30", "Y": "40"},
    },
    "shower_bath_5x8": {
        "about": "5' x 8' bath with a walk-in shower across the end instead of a tub.",
        "PROJECT": {"ROOM_NAME": "Guest Bath"},
        "ROOM": {"WIDTH": "60", "LENGTH": "96"},
        "VANITY": {"INCLUDE": "yes", "WIDTH": "30", "WALL": "W", "POSITION": "81", "SINKS": "1"},
        "TOILET": {"INCLUDE": "yes", "WALL": "W", "POSITION": "51"},
        "TUB": {"INCLUDE": "no"},
        "SHOWER": {"INCLUDE": "yes", "WALL": "S", "POSITION": "30", "WIDTH": "60", "DEPTH": "34"},
        "SHOWER_TRIM": {"INCLUDE": "yes", "WALL": "S", "POSITION": "30", "OFF_FLOOR": "42"},
        "DOOR": {"INCLUDE": "yes", "WALL": "E", "POSITION": "24", "SWING": "right"},
        "WINDOW": {"INCLUDE": "no"},
        "CEILING_LIGHT": {"X": "30", "Y": "40"},
    },
    "primary_bath_10x12": {
        "about": "10' x 12' primary bath: double vanity, freestanding tub under a window, "
                 "walk-in shower, toilet.",
        "PROJECT": {"ROOM_NAME": "Primary Bath"},
        "ROOM": {"WIDTH": "120", "LENGTH": "144", "CEILING": "108"},
        "VANITY": {"INCLUDE": "yes", "WIDTH": "72", "WALL": "N", "POSITION": "60", "SINKS": "2"},
        "MIRROR": {"COUNT": "2"},
        "TOILET": {"INCLUDE": "yes", "WALL": "W", "POSITION": "84"},
        "TUB": {"INCLUDE": "yes", "TYPE": "freestanding", "WALL": "E", "POSITION": "90", "OFF_WALL": "8"},
        "SHOWER": {"INCLUDE": "yes", "WALL": "W", "POSITION": "30", "WIDTH": "60", "DEPTH": "42"},
        "SHOWER_TRIM": {"INCLUDE": "yes", "WALL": "W", "POSITION": "30", "OFF_FLOOR": "42"},
        "DOOR": {"INCLUDE": "yes", "WALL": "S", "POSITION": "58", "WIDTH": "30", "SWING": "left"},
        "WINDOW": {"INCLUDE": "yes", "WALL": "E", "POSITION": "90", "WIDTH": "36", "HEIGHT": "48",
                   "OFF_FLOOR": "42"},
        "CEILING_LIGHT": {"X": "60", "Y": "72"},
        "WALL_TILE": {"HEIGHT": "0"},
        "FLOOR_TILE": {"TILE_SIZE": "48x24"},
    },
    "powder_room_5x6": {
        "about": "5' x 6' powder room: vanity and toilet, no tub or shower.",
        "PROJECT": {"ROOM_NAME": "Powder Room"},
        "ROOM": {"WIDTH": "60", "LENGTH": "72"},
        "VANITY": {"INCLUDE": "yes", "WIDTH": "24", "WALL": "N", "POSITION": "20", "SINKS": "1"},
        "TOILET": {"INCLUDE": "yes", "WALL": "E", "POSITION": "38"},
        "TUB": {"INCLUDE": "no"},
        "SHOWER": {"INCLUDE": "no"},
        "SHOWER_TRIM": {"INCLUDE": "no"},
        "SHOWER_TILE": {},
        "DOOR": {"INCLUDE": "yes", "WALL": "S", "POSITION": "42", "SWING": "right"},
        "WINDOW": {"INCLUDE": "no"},
        "CEILING_LIGHT": {"X": "30", "Y": "30"},
        "WALL_TILE": {"HEIGHT": "full", "TILE_SIZE": "8x2"},
    },
}


# ---------------------------------------------------------------- file I/O

def template_values(name):
    if name not in TEMPLATES:
        raise SystemExit(f"Unknown template '{name}'. Options: {', '.join(TEMPLATES)}")
    values = {}
    for section in ["PROJECT", "ROOM", *FIXTURES, *TILES, "SETTINGS"]:
        values[section] = {**COMMON.get(section, {}), **TEMPLATES[name].get(section, {})}
    return values


def section_fields(section):
    if section == "PROJECT":
        return PROJECT_FIELDS
    if section == "ROOM":
        return ROOM_FIELDS
    if section == "SETTINGS":
        return SETTINGS_FIELDS
    if section in TILES:
        return TILE_FIELDS
    fields = [f for f in FIXTURE_FIELDS if not (section in NO_POSITION and f in ("WALL", "POSITION"))]
    return fields + EXTRA_FIELDS.get(section, [])


HEADER = """# =====================================================================
#  BATHROOM DESIGNER - project file ({template})
#  {about}
#
#  Fill in / change what you want, save, then run:   python build.py {filename}
#  Output: a PDF sheet set in the output/ folder.
#
#  All sizes are in INCHES (34.5 or 34 1/2 both work).
#  URL      = product page link.  FINISH = finish name as the site shows it.
#  WIDTH / HEIGHT / DEPTH: leave blank to read them from the website.
#  INCLUDE  = yes / no  (no = leave this item out).
#  WALL     = N, E, S or W  (north = top of the floor plan).
#  POSITION = inches from the LEFT corner of that wall (standing in the
#             room, facing the wall) to the CENTER of the item.
#  OFF_FLOOR = inches from the floor to the bottom of the item.
#
#                         N (top)
#               +---------------------+
#               |                     |
#          W    |        ROOM         |    E
#               |                     |
#               +---------------------+
#                           S
# =====================================================================
"""


def template_size(template, section):
    """Template's size for a fixture, used only when the website doesn't give one."""
    t = TEMPLATES.get(template or "", {}).get(section, {})
    return {k: to_inches(t.get(k)) for k in SIZE_FIELDS if t.get(k)}


def write_project(path, template):
    values = template_values(template)
    values["PROJECT"]["TEMPLATE"] = template
    out = [HEADER.format(template=template, about=TEMPLATES[template]["about"], filename=path.name)]
    for section, fields in values.items():
        out.append(f"\n[{section}]")
        help_text = SECTION_HELP.get(section, "")
        for line in help_text.splitlines():
            out.append(f"# {line}")
        names = section_fields(section)
        pad = max(len(n) for n in names)
        for name in names:
            value = fields.get(name, "")
            if name in SIZE_FIELDS and section in FIXTURES and section not in FIXED_SIZE and value:
                # a template size is only a fallback: the product's real size should win
                out.append(f"{name.ljust(pad)} =          # blank = from website, else {value}")
                continue
            out.append(f"{name.ljust(pad)} = {value}".rstrip() + (" " if not value else ""))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def read_project(path):
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
            items[section][key.strip().upper()] = value.split(" #")[0].strip()
    return items


def included(entry):
    return bool(entry) and entry.get("INCLUDE", "yes").strip().lower() not in ("no", "n", "false", "0", "")


def parse_tile_size(text, default):
    nums = re.findall(r"\d+(?:\.\d+)?", text or default)
    if len(nums) < 2:
        nums = re.findall(r"\d+(?:\.\d+)?", default)
    return float(nums[0]), float(nums[1])


# ---------------------------------------------------------------- geometry

@dataclass
class Room:
    width: float     # x, west-east
    length: float    # y, north-south
    ceiling: float

    def wall_len(self, wall):
        return self.width if wall in ("N", "S") else self.length

    def to_plan(self, wall, u, b):
        """Wall-local point (u from the wall's left corner, b inches into the
        room) -> plan (x, y)."""
        if wall == "N":
            return u, b
        if wall == "S":
            return self.width - u, self.length - b
        if wall == "E":
            return self.width - b, u
        return b, self.length - u                   # W

    def to_wall(self, wall, x, y):
        """Plan (x, y) -> (u along the wall from its left corner, distance from the wall)."""
        if wall == "N":
            return x, y
        if wall == "S":
            return self.width - x, self.length - y
        if wall == "E":
            return y, self.width - x
        return self.length - y, x                   # W

    def left_wall(self, wall):
        """Wall at the left end of `wall` when facing it."""
        return WALLS[(WALLS.index(wall) - 1) % 4]

    def right_wall(self, wall):
        return WALLS[(WALLS.index(wall) + 1) % 4]


@dataclass
class Item:
    """One product (possibly placed several times, e.g. 2 faucets)."""
    section: str
    kind: str
    w: float
    h: float
    d: float
    finish: str = ""
    url: str = ""
    name: str = ""
    img: object = None           # cut-out RGBA photo or None (draw a generic shape)
    color: tuple = None          # main color
    extra: dict = field(default_factory=dict)

    @property
    def label(self):
        return self.section.replace("_", " ").title()


@dataclass
class Placed:
    item: Item
    wall: str                    # wall it's on / against
    u: float                     # center, inches from the wall's left corner
    bottom: float                # inches off the floor
    off_wall: float = 0.0
    x: float = None              # plan center (ceiling lights)
    y: float = None

    @property
    def kind(self):
        return self.item.kind

    def footprint(self, room):
        """Plan rectangle (x0, y0, x1, y1)."""
        it = self.item
        if self.x is not None:
            return (self.x - it.w / 2, self.y - it.d / 2, self.x + it.w / 2, self.y + it.d / 2)
        a = room.to_plan(self.wall, self.u - it.w / 2, self.off_wall)
        b = room.to_plan(self.wall, self.u + it.w / 2, self.off_wall + it.d)
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

    @property
    def top(self):
        return self.bottom + self.item.h


FLOOR_KINDS = {"vanity", "toilet", "tub", "shower"}
WALL_OPENINGS = {"door", "window"}


def place_items(cfg, items, room, settings):
    """Turn project settings into positioned fixtures."""
    placed = []

    def num(entry, key, default=None):
        v = to_inches(entry.get(key))
        return default if v is None else v

    vanity_p = None
    if "VANITY" in items:
        e, it = cfg["VANITY"], items["VANITY"]
        vanity_p = Placed(it, e.get("WALL", "N").upper() or "N", num(e, "POSITION", room.width / 2),
                          num(e, "OFF_FLOOR", 0))
        placed.append(vanity_p)

    sink_us, faucet_top = [], None
    if vanity_p:
        n = max(1, int(num(cfg["VANITY"], "SINKS", 1)))
        left = vanity_p.u - vanity_p.item.w / 2
        sink_us = [left + vanity_p.item.w * (i + 0.5) / n for i in range(n)]
        vanity_p.item.extra["sinks"] = sink_us

        if "FAUCET" in items:
            f = items["FAUCET"]
            bottom = num(cfg["FAUCET"], "OFF_FLOOR", vanity_p.top)
            for u in sink_us:
                placed.append(Placed(f, vanity_p.wall, u, bottom))
            faucet_top = bottom + f.h

        mirrors = []
        if "MIRROR" in items:
            m = items["MIRROR"]
            count = int(num(cfg["MIRROR"], "COUNT", len(sink_us)))
            us = sink_us if count == len(sink_us) else \
                [left + vanity_p.item.w * (i + 0.5) / count for i in range(count)]
            gap = num(settings, "MIRROR_GAP", 6)
            base = (faucet_top or vanity_p.top + 8) + gap
            bottom = num(cfg["MIRROR"], "OFF_FLOOR", max(base, 40))
            for u in us:
                p = Placed(m, vanity_p.wall, u, bottom)
                placed.append(p)
                mirrors.append(p)

        if "VANITY_LIGHT" in items:
            vl = items["VANITY_LIGHT"]
            for mp in mirrors or [vanity_p]:
                top = mp.top if mp is not vanity_p else vanity_p.top + 40
                placed.append(Placed(vl, vanity_p.wall, mp.u, num(cfg["VANITY_LIGHT"], "OFF_FLOOR", top + 4)))

        if "SCONCES" in items:
            s = items["SCONCES"]
            bottom = num(cfg["SCONCES"], "OFF_FLOOR", 66 - s.h / 2)
            spots = []
            for mp in mirrors or [vanity_p]:
                half = mp.item.w / 2 if mp is not vanity_p else 12
                spots += [mp.u - half - s.w / 2 - 4, mp.u + half + s.w / 2 + 4]
            spots.sort()
            merged = []
            for u in spots:                      # sconces between two mirrors share one spot
                if merged and u - merged[-1] < s.w + 6:
                    merged[-1] = (merged[-1] + u) / 2
                else:
                    merged.append(u)
            wall_len = room.wall_len(vanity_p.wall)
            for u in merged:
                if s.w / 2 + 2 <= u <= wall_len - s.w / 2 - 2:     # skip one that would be past the corner
                    placed.append(Placed(s, vanity_p.wall, u, bottom))

    for section in ("TOILET", "SHOWER", "DOOR", "WINDOW", "SHOWER_TRIM"):
        if section in items:
            e, it = cfg[section], items[section]
            wall = (e.get("WALL") or "N").upper()
            bottom = num(e, "OFF_FLOOR", {"window": 42, "shower_trim": 42}.get(it.kind, 0))
            placed.append(Placed(it, wall, num(e, "POSITION", room.wall_len(wall) / 2), bottom))

    if "TUB" in items:
        e, it = cfg["TUB"], items["TUB"]
        wall = (e.get("WALL") or "S").upper()
        free = it.extra.get("type") == "freestanding"
        placed.append(Placed(it, wall, num(e, "POSITION", room.wall_len(wall) / 2), 0,
                             off_wall=num(e, "OFF_WALL", 8 if free else 0)))

    if "CEILING_LIGHT" in items:
        e, it = cfg["CEILING_LIGHT"], items["CEILING_LIGHT"]
        placed.append(Placed(it, "N", 0, room.ceiling - it.h,
                             x=num(e, "X", room.width / 2), y=num(e, "Y", room.length / 2)))
    return placed


# ---------------------------------------------------------------- checks

def check_layout(room, placed):
    """Basic fit and code-clearance checks (IRC/CPC minimums)."""
    notes = []
    floor = [p for p in placed if p.kind in FLOOR_KINDS]
    for p in floor:
        x0, y0, x1, y1 = p.footprint(room)
        if x0 < -0.5 or y0 < -0.5 or x1 > room.width + 0.5 or y1 > room.length + 0.5:
            notes.append(f"{p.item.label} runs past the room walls - check WALL / POSITION / size.")
    for i, a in enumerate(floor):
        for b in floor[i + 1:]:
            ax0, ay0, ax1, ay1 = a.footprint(room)
            bx0, by0, bx1, by1 = b.footprint(room)
            if min(ax1, bx1) - max(ax0, bx0) > 0.5 and min(ay1, by1) - max(ay0, by0) > 0.5:
                notes.append(f"{a.item.label} overlaps {b.item.label}.")

    for p in placed:
        if p.kind == "toilet":
            side = min(side_clearance(room, p, floor, left) for left in (True, False))
            if side < 15:
                notes.append(f"Toilet centerline is {side:.1f}\" from the nearest wall/fixture "
                             "(code minimum 15\").")
            front = front_clearance(room, p, floor)
            if front < 21:
                notes.append(f"Toilet has {front:.1f}\" clear in front (code minimum 21\", 30\" recommended).")
        if p.kind == "vanity":
            front = front_clearance(room, p, floor)
            if front < 21:
                notes.append(f"Vanity has {front:.1f}\" clear in front (code minimum 21\", 30\" recommended).")
            for u in p.item.extra.get("sinks", []):
                if min(u, room.wall_len(p.wall) - u) < 15:
                    notes.append("A sink centerline is less than 15\" from a side wall (code minimum).")
        if p.kind == "shower" and min(p.item.w, p.item.d) < 30:
            notes.append("Shower is smaller than the 30\" x 30\" code minimum.")
    return notes


def side_clearance(room, p, floor, left):
    """Distance from p's centerline to the nearest wall/fixture on one side, along its wall."""
    best = p.u if left else room.wall_len(p.wall) - p.u
    for q in floor:
        if q is p:
            continue
        x0, y0, x1, y1 = q.footprint(room)
        us = [room.to_wall(p.wall, x, y) for x, y in ((x0, y0), (x1, y1))]
        qu0, qu1 = sorted(u for u, _ in us)
        qb0 = min(b for _, b in us)
        if qb0 > p.item.d:                       # not beside it
            continue
        if left and qu1 <= p.u:
            best = min(best, p.u - qu1)
        if not left and qu0 >= p.u:
            best = min(best, qu0 - p.u)
    return best


def front_clearance(room, p, floor):
    """Clear distance in front of p to the opposite wall or another fixture."""
    depth_room = room.length if p.wall in ("N", "S") else room.width
    front = p.off_wall + p.item.d
    best = depth_room - front
    u0, u1 = p.u - p.item.w / 2, p.u + p.item.w / 2
    for q in floor:
        if q is p:
            continue
        x0, y0, x1, y1 = q.footprint(room)
        us = [room.to_wall(p.wall, x, y) for x, y in ((x0, y0), (x1, y1))]
        qu0, qu1 = sorted(u for u, _ in us)
        qb0 = min(b for _, b in us)
        if qu1 > u0 and qu0 < u1 and qb0 >= front - 0.5:
            best = min(best, qb0 - front)
    return best

"""Read a floor plan (PDF, photo, scan, or hand sketch) with Gemini and turn it
into project-file values: room size plus where each fixture sits.

Gemini reports every fixture as a box in plan inches measured from the room's
top-left inside corner (top of the page = north). Converting those boxes to
WALL / POSITION / size is done here, so the math is exact and consistent.
"""

import base64
import json
import re

from gemini import generate, get_key, save_key
from project import Room

DEFAULT_MODEL = "gemini-3.8-flash"     # swapped automatically if Google retires it
MAX_BYTES = 18 * 1024 * 1024          # inline upload limit (~20 MB per request)
KINDS = ["vanity", "toilet", "tub", "shower", "door", "window"]

PROMPT = """You are reading an architectural floor plan, scanned plan, photo of a plan, or a
hand-drawn field sketch of a residential bathroom.
{room_hint}
Find the bathroom and report its interior size and fixtures as JSON (no other text).

Coordinate system: inches, origin at the TOP-LEFT inside corner of the room as the plan
is shown (ignore any north arrow). x increases to the right, y increases downward.
width_in = interior size left-right, length_in = interior size top-bottom.

Rules:
- Use the WRITTEN dimensions on the plan whenever they exist (e.g. 8'-6" = 102 in).
  Convert feet-inches to inches. Use the drawing scale or proportions only when no
  dimension is written, and say so in "estimated".
- For each fixture give its footprint box [x0, y0, x1, y1] in the room coordinates above.
- vanity: note number of sinks. tub: "alcove" if walls on three sides, else "freestanding".
- toilet: type if you can tell ("one-piece", "two-piece", "wall-hung"), else null.
- door: box of the opening in the wall, plus hinge_xy = the hinge point, if a swing arc
  is drawn.
- window: box of the opening in the wall.
- ceiling_in only if a ceiling height is written, else null.
- Do not invent fixtures that are not drawn.

JSON shape:
{{"room_name": str, "width_in": number, "length_in": number, "ceiling_in": number|null,
  "fixtures": [{{"type": "vanity|toilet|tub|shower|door|window", "box": [x0,y0,x1,y1],
                 "sinks": int|null, "tub_type": str|null, "toilet_type": str|null,
                 "hinge_xy": [x,y]|null, "label": str|null}}],
  "estimated": [str], "notes": [str]}}
"""


def call_gemini(data, mime, key, model, room_hint=""):
    hint = f'If the plan shows several rooms, read only: "{room_hint}".' if room_hint else ""
    body = {
        "contents": [{"parts": [
            {"text": PROMPT.format(room_hint=hint)},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    result = generate(body, key, model, "text", timeout=180)
    parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("Gemini didn't return plan data. Try a clearer image or type the room name.")
    return json.loads(m.group(0))


def nearest_wall(room, box):
    """Wall the fixture backs onto. In a corner (touching two walls) pick the wall its
    long side runs along, e.g. a tub across the end of the room."""
    x0, y0, x1, y1 = box
    gaps = {"N": y0, "S": room.length - y1, "W": x0, "E": room.width - x1}
    close = [w for w in gaps if gaps[w] <= min(gaps.values()) + 1]
    along = {"N": x1 - x0, "S": x1 - x0, "W": y1 - y0, "E": y1 - y0}
    wall = max(close, key=lambda w: along[w])
    return wall, max(0.0, gaps[wall])


def along_and_depth(room, wall, box):
    """Box -> (center along the wall from its left corner, width along the wall, depth into the room)."""
    x0, y0, x1, y1 = box
    a = room.to_wall(wall, x0, y0)
    b = room.to_wall(wall, x1, y1)
    u0, u1 = sorted((a[0], b[0]))
    d0, d1 = sorted((a[1], b[1]))
    return (u0 + u1) / 2, u1 - u0, d1 - d0


def r(x):
    return f"{round(x * 2) / 2:g}"          # nearest 1/2"


def to_values(ext):
    """Gemini's plan data -> {section: {field: value}} for the form, plus notes."""
    width, length = float(ext["width_in"]), float(ext["length_in"])
    room = Room(width, length, float(ext.get("ceiling_in") or 96))
    values = {"ROOM": {"WIDTH": r(width), "LENGTH": r(length)}}
    if ext.get("ceiling_in"):
        values["ROOM"]["CEILING"] = r(float(ext["ceiling_in"]))
    if ext.get("room_name"):
        values["PROJECT"] = {"ROOM_NAME": str(ext["room_name"])[:40]}
    notes = [f"Estimated: {e}" for e in ext.get("estimated") or []] + list(ext.get("notes") or [])

    found = {}
    for f in ext.get("fixtures") or []:
        kind = str(f.get("type", "")).lower()
        box = f.get("box")
        if kind not in KINDS or not box or len(box) != 4:
            continue
        box = [max(0.0, min(float(v), lim)) for v, lim in zip(box, (width, length, width, length))]
        if box[2] - box[0] < 0.5 and box[3] - box[1] < 0.5:
            continue
        found.setdefault(kind, []).append((f, box))

    for kind in KINDS:
        section = kind.upper()
        if kind not in found:
            if kind != "door":
                values[section] = {"INCLUDE": "no"}
                if kind == "vanity":
                    values["VANITY_2"] = {"INCLUDE": "no"}
            else:
                notes.append("No door found on the plan - kept the layout's door. Check it.")
            continue
        items = found[kind]
        items.sort(key=lambda it: -(it[1][2] - it[1][0]) * (it[1][3] - it[1][1]))      # largest first
        if kind == "vanity":
            values["VANITY_2"] = {"INCLUDE": "no"}
            if len(items) >= 2:
                values["VANITY_2"] = fixture_values(room, kind, *items[1])
            if len(items) > 2:
                notes.append(f"{len(items)} vanities on the plan; the form has two - used the two largest.")
        elif len(items) > 1:
            notes.append(f"{len(items)} {kind}s on the plan; the form has one - used the largest.")
        f, box = items[0]
        values[section] = fixture_values(room, kind, f, box)

    # things that follow from the room and the wet areas
    # things that follow from the room and the wet areas
    values["CEILING_LIGHT"] = {"X": r(width / 2), "Y": r(length / 2)}
    if not ext.get("ceiling_in"):
        values["ROOM"]["CEILING"] = "96"
        notes.append("No ceiling height on the plan - assumed the standard 8'-0\". Change it in Room if needed.")
    trim = {"INCLUDE": "no"}
    if "shower" in found:
        s = values["SHOWER"]
        trim = {"INCLUDE": "yes", "WALL": s["WALL"], "POSITION": s["POSITION"]}
    elif "tub" in found and values["TUB"].get("TYPE") == "alcove":
        # tub/shower combo: valve on an end wall of the tub
        _, box = found["tub"][0]
        tub_wall = values["TUB"]["WALL"]
        for end in (room.left_wall(tub_wall), room.right_wall(tub_wall)):
            u, _, depth = along_and_depth(room, end, box)
            if nearest_gap(room, end, box) < 1:
                trim = {"INCLUDE": "yes", "WALL": end, "POSITION": r(u)}
                break
    values["SHOWER_TRIM"] = trim
    values["MIRROR"] = {"COUNT": ""}                      # one mirror per sink
    for acc in ("TP_HOLDER", "TOWEL_BAR", "TOWEL_BAR_2", "TOWEL_RING", "TOWEL_RING_2", "ROBE_HOOK", "ROBE_HOOK_2"):
        values[acc] = {"INCLUDE": "no", "WALL": "", "POSITION": ""}   # you add these and choose where
    return values, notes


def fixture_values(room, kind, f, box):
    """One fixture box -> its form fields."""
    wall, gap = nearest_wall(room, box)
    u, along, depth = along_and_depth(room, wall, box)
    v = {"INCLUDE": "yes", "WALL": wall, "POSITION": r(u), "WIDTH": r(along)}
    if kind in ("vanity", "toilet", "tub", "shower"):
        v["DEPTH"] = r(depth)
    if kind == "vanity":
        v["SINKS"] = "2" if (f.get("sinks") or 1) >= 2 else "1"
    if kind == "toilet":
        v.pop("WIDTH")                    # plans draw toilets generic; keep the product's width
        if f.get("toilet_type") in ("one-piece", "two-piece", "wall-hung"):
            v["TYPE"] = f["toilet_type"]
    if kind == "tub":
        free = (f.get("tub_type") == "freestanding") or gap > 3
        v["TYPE"] = "freestanding" if free else "alcove"
        if free:
            v["OFF_WALL"] = r(gap)
    if kind == "door":
        hinge = f.get("hinge_xy")
        if hinge and len(hinge) == 2:
            hu, _ = room.to_wall(wall, float(hinge[0]), float(hinge[1]))
            v["SWING"] = "left" if hu < u else "right"
    return v


def nearest_gap(room, wall, box):
    x0, y0, x1, y1 = box
    return {"N": y0, "S": room.length - y1, "W": x0, "E": room.width - x1}[wall]


def read_plan(data, mime, key, model=None, room_hint=""):
    key = get_key(key)
    if not key:
        raise RuntimeError("Reading plans needs a Gemini API key (Settings > Gemini API key, "
                           "or the GEMINI_API_KEY environment variable).")
    if len(data) > MAX_BYTES:
        raise RuntimeError("That file is over 18 MB. Export just the bathroom page, or a smaller image.")
    ext = call_gemini(data, mime, key, model or DEFAULT_MODEL, room_hint)
    save_key(key)                      # worked - remember it so it only has to be pasted once
    values, notes = to_values(ext)
    return {"values": values, "notes": notes, "raw": ext}

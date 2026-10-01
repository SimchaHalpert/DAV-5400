# Bathroom Designer

Paste product links into a project file and get a client-ready **11x17 PDF sheet set**:

| Sheet | What it is |
|---|---|
| Rendering | 3D-style view of the room built from your actual product photos |
| AI Rendering + Bird's-Eye | Photo-real images from Google Gemini (optional, needs a key) |
| Floor Plan | Colored bird's-eye plan + dimensioned line plan, to scale |
| Elevations | Each wall, colored + dimensioned line drawing, to scale |
| Fixture Schedule | Every product with photo, finish, size, qty, source, and code-clearance checks |

Every page has a title block: logo, company, client, project, room, sheet number, date.

## Setup (one time)
```
pip install -r requirements.txt
```
Optional, for much cleaner product cut-outs: `pip install "rembg[cpu]"`

Put your logo in this folder as `logo.png`.

## Updating
Stop the app (Ctrl + C), then in this folder run:
```
./update.sh
```
It downloads the latest version and keeps your projects, output, saved key, and setup.

## Easiest: the web form
```
python app.py
```
Your browser opens a form (it runs only on your computer). Pick a layout, click **New from layout**,
fill in the client and product links, and use the dropdowns for walls, tub type, door swing, tile
pattern and direction, etc. The floor plan preview on the right updates as you change things and
flags clearance problems. Click **Build PDF**, then **Open PDF**. Projects save to the `projects/`
folder and can be reopened from the dropdown at the top.

### Upload a plan
In the web form, **Upload a plan** takes a PDF, photo, scan, or hand sketch. Type which room if the plan
shows more than one, and click **Read plan**. Gemini reads the written dimensions and fixture locations,
and the form fills in: room size, and wall / position / size for the vanity, toilet, tub, shower, door,
and window. Everything it changed is highlighted in yellow, and anything it estimated (instead of reading
a written dimension) is listed under the plan. Check those, then Build.
Needs the Gemini API key (same one as the AI renderings) in Settings or the `GEMINI_API_KEY` variable.
Tip: for a big construction set, export just the bathroom page (under 18 MB).

## Or: edit the text file directly
1. Pick a starting layout:
   ```
   python build.py --templates
   python build.py my_client.txt --new primary_bath_10x12
   ```
   Layouts: `hall_bath_5x8`, `shower_bath_5x8`, `primary_bath_10x12`, `powder_room_5x6`.
2. Open the new file. Fill in `CLIENT`, `PROJECT`, and for each item its `URL` and `FINISH`.
   Tweak the room size and positions if needed (`WALL` + `POSITION`, explained at the top of the file).
   Set `INCLUDE = no` for anything the room doesn't have.
3. Build:
   ```
   python build.py my_client.txt
   ```
4. Open the PDF in the `output/` folder.

Items without a link are drawn as generic shapes at standard sizes, so you can lay out the
room first and add products later.

## AI renderings (Gemini)
1. Get an API key at https://aistudio.google.com/apikey
2. Either set it once on your computer: `export GEMINI_API_KEY=your-key` (Mac) /
   `setx GEMINI_API_KEY your-key` (Windows), or paste it into `GEMINI_API_KEY` in the project file
   (don't send that file to clients if you do).
3. Build as usual. Two AI sheets are added. Skip them with `--no-ai`.

Default model is `gemini-2.5-flash-image`. For higher quality, set `GEMINI_MODEL` to a newer Gemini
image model. Each build makes 2 images (usually a few cents to ~$0.30 total, depending on the model).
AI images are for design intent. They may not match products exactly, so the product-photo
rendering and drawings are the reference.

## Toilet and accessories
- Toilet `TYPE`: one-piece, two-piece, or wall-hung. Wall-hung shows the flush plate and in-wall
  carrier in the elevations and a tankless plan symbol.
- TP holder, towel bars, towel rings, robe hooks: each has a link, finish, wall, position, and height
  (`OFF_FLOOR`, blank = standard: TP 26", towel bar 48", ring 54", hook 66" to center).
  The TP holder goes next to the toilet automatically if its wall/position are blank.
- There's a second towel bar / ring / hook. Leave its link blank to use the same product as the first.

## What the checks look at
Toilet centerline at least 15" from walls/fixtures and 21" clear in front, vanity 21" clear in front,
sink centerline 15" from side walls, 30"x30" minimum shower, fixtures overlapping or running past walls,
accessories placed inside a shower or tub area.
Warnings print in the terminal and on the schedule sheet in red.

## Tips
- Terminal output shows where each size came from: `website`, `project file`, `photo`, or
  `standard size`. If it's not `website` for a key item, type the real size into the project file.
- Tile (wall, shower, floor): paste a tile product link (or just a `COLOR`) and set:
  - `TILE_SIZE`: e.g. `3x12`, `24x48` (order doesn't matter)
  - `PATTERN`: stack, offset 1/2, offset 1/3, offset 1/4, herringbone, double herringbone,
    chevron, basketweave, diagonal stack, diagonal offset
  - `DIRECTION` (which way the long side runs): walls `horizontal` / `vertical`;
    floor `along room length`, `across room width`, `east-west`, or `north-south`
  - `HEIGHT` for wall and shower tile: inches, `0` (paint only), or `full`
- `RENDER_WALL` picks which wall the rendering looks at (default: the vanity wall).
- Some big retailers (Home Depot, Lowe's, Wayfair) block automated requests. If an item fails,
  right-click the product photo, choose **Copy image address**, paste that as the `URL`, and type
  the size in.

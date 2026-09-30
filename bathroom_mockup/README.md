# Bathroom Mockup

Paste 3 product links (vanity, faucet, mirror) and a finish for each. You get one PNG:
products cut out of their photos, scaled to real inches, and placed on a wall the way
they'd be installed, with labels and a height ruler.

## Setup (one time)
```
pip install -r requirements.txt
```
Optional, for much cleaner cut-outs: `pip install "rembg[cpu]"`

## Use
1. Open `products.txt` and fill in `[VANITY]`, `[FAUCET]`, `[MIRROR]`:
   - `URL`: product page link
   - `FINISH`: finish name as the site shows it (Matte Black, Brushed Gold...)
   - `HEIGHT` / `WIDTH` (inches): optional, only if the site is missing or wrong
   - `OFF_FLOOR` (inches): optional, for floating vanities or wall-mount faucets
2. Run `python mockup.py`
3. Open `mockup.png`

The terminal shows which photo it picked, each size, and where the size came from
(website / products.txt / estimate). If it says "estimate", fill in `HEIGHT`.

## Layout
- Vanity on the floor (or `OFF_FLOOR`)
- Faucet on the vanity top, centered
- Mirror `MIRROR_GAP` inches above the faucet, never lower than 40" off the floor
- Left ruler: counter height, faucet top, mirror bottom and top
- Right labels: product name, finish, W x H

`[SETTINGS]` in `products.txt` changes wall/floor color, mirror gap, labels on/off,
and resolution. `WALL_COLOR = none` gives a transparent background.

## How it picks things
- **Photo:** the one labeled with your finish; among those, a plain studio shot
  over a room shot (room shots don't cut out cleanly).
- **Size:** "Overall / Product Height" over parts like spout, handle, cabinet,
  toe kick, or package size. Out-of-range numbers are ignored.
- **Angled photos:** if the photo's shape doesn't match the listed W x H, it
  sizes by height and tells you.

## If a site fails
Big retailers (Home Depot, Lowe's, Wayfair) sometimes block automated requests.
Right-click the product photo, choose **Copy image address**, paste that as `URL`,
and fill in `HEIGHT`.

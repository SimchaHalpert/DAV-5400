# Bathroom Mockup

Paste 3 product links (vanity, faucet, mirror). You get one transparent PNG with each
product's background removed, every item scaled to its real size, and the items stacked
the way they're installed.

## Setup (one time)
```
pip install -r requirements.txt
```
Optional, for much cleaner background removal: `pip install "rembg[cpu]"`

## Use
1. Open `products.txt` and fill in the blanks under `[VANITY]`, `[FAUCET]`, `[MIRROR]`:
   - `URL`: the product page link
   - `FINISH`: the finish name as the site shows it (Matte Black, Brushed Gold...)
   - `HEIGHT` / `WIDTH` (inches): optional, only if the site doesn't list them or lists them wrong
2. Run `python mockup.py`
3. Open `mockup.png`

## How it's laid out
- Vanity sits on the floor
- Faucet sits on the vanity top, centered
- Mirror is 6" above the faucet (and at least 40" off the floor)
- Scale: 20 px = 1 inch. You can change these numbers at the top of `mockup.py`.

## If a site fails
Some big retailers (Home Depot, Lowe's, Wayfair) block automated requests. If that
happens, right-click the product photo, choose **Copy image address**, paste that into
`URL`, and fill in `HEIGHT`.

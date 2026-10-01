# Where we left off (Oct 1, 2026)

## Done and working on the Mac
- Web form (`python3 app.py`), PDF sheet set, tile patterns, toilet types, accessories
- Plan upload read by Gemini (room size, fixtures, two vanities, 8' default ceiling)
- Gemini model auto-switch + busy retries (stops after ~1.5 min)
- Accessories are now opt-in (off by default, also after reading a plan);
  Auto spots are reported in the notes
- Update on the Mac with `./update.sh`, then `python3 app.py`

## Open items
1. **Drag accessories on a wall view** (requested, not started): pick a wall, see its
   elevation, drag TP holder / towel bars / rings / hooks left-right and up-down,
   snapping to 1/2", fields update on drop.
2. **Door landed in the wrong place** after reading the plan. Waiting on: screenshot of
   plan vs. preview, and whether the notes said "No door found on the plan".
   If Gemini missed the door, the starting layout's door is kept; fix the prompt
   and/or ask before keeping it.
3. Try real product links (vanity, faucet, toilet, tile) and see which sites work.

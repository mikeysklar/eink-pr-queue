# case

3D printed SKÅDIS-mount case: bare 4.2" eInk panel on a raised deck, FeatherWing
Doubler stack in an open tray below it, matching the photo layout. Two printed
parts: `base` (back plate, tray, deck) and `bezel` (glass retainer).

Built with [kicad2freecad-enclosures](https://github.com/mikeysklar/kicad2freecad-enclosures)
around the doubler PCB. Adafruit ships the doubler as Eagle only, so the board
is converted first.

## Rebuild

```
KICAD=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
FC=/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
TOOLS=/Users/sklarm/src/kicad2freecad-enclosures
B=$(pwd)

kicad-cli pcb import DoubleWing.brd -o $B/build/doubler.kicad_pcb   # once
$KICAD $TOOLS/extract_board.py $B/build/doubler.kicad_pcb -o $B/build/board.json
$FC $TOOLS/assemble.py -- --params $B/params.json --board $B/build/board.json --out $B/build --check
$FC $TOOLS/check.py    -- --params $B/params.json --board $B/build/board.json --out $B/build --svg
```

Or just open `build/enclosure.FCStd` in FreeCAD and edit the `params`
spreadsheet; recompute rebuilds the solids. Every value in `params.json` is a
live cell.

## Key numbers

| What | Value | Source |
|---|---|---|
| Glass | 91.0 x 77.0 x 1.2 | ZJY400300-042CAAMFGN datasheet |
| Active area | 84.8 x 63.6, border 3.1 / 10.3 on FPC edge | datasheet drawing p.5 |
| FPC tail | 12.5 wide, 18.5 long, exits 16.8-29.3 from corner | datasheet drawing p.5 |
| Doubler | 50.85 x 47.04, eight 2.5 mm holes | extracted from DoubleWing.brd |
| Tray vs glass | 10 mm left (`panel_offset`), 8 mm gap (`panel_gap`) | ribbon strain, measured on the v1 print |
| Lightening | 31 windows, base 93.8 to 68.8 cm3 | `lighten_mode` in params |
| SKÅDIS | 5x15 slots, 40 mm grid, columns staggered 20 | community-measured |
| Deck face | Z 17.0, just under the wing-top plane | `panel_deck_z` |

## Assembly

1. M4 bolts through the two counterbored holes in the deck, into the SKÅDIS
   board (T-nut or washer+nut behind). They vanish under the glass later.
2. Two more M4 through the exposed slots beside the tray.
3. Heat-set M2.5 inserts (3.5 mm OD) into the 8 standoffs; screw the doubler down.
4. Wings into the doubler sockets, ribbon side up, ZIF toward the panel.
5. Drop the glass into the pocket, ribbon over the notch, into the ZIF.
6. Bezel on with four M2.5 self-tappers.

Print both parts flat, back on the bed, no supports - the deck skin bridges
rib to rib and the rib/wall window ceilings are short bridges too (all inside
the declared `bridge_zones`).

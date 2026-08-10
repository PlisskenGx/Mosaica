# Mosaic Engine

A standalone core for converting artwork into a buildable tile mosaic.

## Current capabilities
- Exact row/column or physical-size targeting
- Real tile dimensions + grout spacing
- Fixed manufacturer palette support via named RGB values / SKUs
- `contain`, `cover`, and `stretch` image fitting
- Nearest-color quantization
- PNG preview
- Tile-count CSV
- Cell-by-cell build-grid CSV

## Example
```bash
python -m mosaic_engine.cli artwork.png \
  --color 'Black:#111111:SKU-BLK' \
  --color 'White:#F4F2EA:SKU-WHT' \
  --tile 1 --grout 0.0625 --width 46 --fit contain --out output
```

## Next engine modules
1. Hex tile geometry and staggered rows
2. True CIE Lab / Delta-E palette matching
3. Floyd-Steinberg / ordered dithering
4. Vector/SVG and printable section maps
5. Palette import from manufacturer catalogs
6. Tile wastage + sheet-pack purchasing calculations
7. Region cleanup / minimum-feature-size controls
8. Interactive desktop/web UI layered over the core engine

# Designer tile families

The static Designer registry contains Hexagon and Square. Hexagon retains its
20/24/28 mm flat-to-flat presets, Point Top and Flat Top orientations, and the
existing None/Solid/Double/Alternating Border catalog. Square owns 16/20/24 mm
side-length presets, Straight orientation, four-neighbor topology, and only
None/Solid Borders.

For a physical-size Square canvas, the requested rectangle is authoritative.
For each axis, Designer chooses the smallest tile-and-grout field whose span
covers the requested dimension, then centers that field on the canvas. Equal
overhang is clipped from opposing sides. This produces deterministic row-major
placements and balanced edge pieces; dimensions compatible with an explicit
counted grid naturally produce only whole tiles. The rule also bounds edge
pieces well above a sliver-sized fraction, so no separate Square visibility
threshold is needed.

Square clipped pieces retain both full parent and visible polygons, but normal
hit testing follows the visible polygon. They remain artwork-eligible when
Border is None. Solid owns the structural clipped perimeter and the first full
interior ring. Manual paint, keyboard navigation, persistence, and flat exports
consume the shared Designer placement model.

Square is intentionally 2D-only. The Fabricate strategy registry still
contains Hexagon alone, so Square Print Package, STL, Studio, and Museum paths
are unavailable before any physical geometry is generated.

## Clipped-tile interaction invariant

Every clipped placement retains its complete parent-tile polygon as an
interaction aid. When the pointer is outside the physical canvas but still
inside that parent polygon, Designer must show the complete dashed parent
outline in the surrounding workspace—not only the fragment inside the canvas.
The root SVG view box therefore includes the full parent bounds. Do not rely on
SVG overflow for this behavior: browsers differ in whether geometry outside a
root view box is painted or receives pointer events.

This is a recurring regression risk when changing canvas fitting, overflow,
layer ordering, or hit testing. Keep the physical canvas boundary authoritative
for exported geometry while preserving the larger interaction bounds for the
Designer helper.

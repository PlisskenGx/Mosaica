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

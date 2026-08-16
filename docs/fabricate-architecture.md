# Mosaica Fabricate architecture

Status: authoritative Phase 1 architecture. Physical testing may supersede
profile dimensions, but not the separation between resolved state, geometry,
and exporters.

## Pipeline and invalidation

```text
DesignerProjectShell / MosaicProject
              |
              v
ResolvedFabricationModel (immutable physical snapshot, millimetres)
              |
              v
SinglePanelGeometry (separate aligned material bodies)
              |
              v
STL now; editable SVG and Bambu/P1S 3MF later
```

Fabricate never rasterizes artwork or replays UI history. The adapter resolves
the current effective tile colors, actual clipped polygons, border ownership,
tile system, grout, and global color identities once. Any meaningful Designer
change makes that snapshot stale; Phase 1 regenerates the complete snapshot.
Incremental invalidation is deliberately deferred.

The snapshot schema and fabrication profile are explicitly versioned. Given
the same resolved project and profile, tile order, mesh construction, body
names, and coordinates are deterministic.

## Coordinates and physical truth

Units are millimetres. The origin is the top-left of the finished artwork at
the flat backside/build-plate plane. X increases right across the artwork, Y
increases down the artwork, and Z increases away from the build plate. Every
body shares this origin. Z=0 is a continuous, flat backside. Printing is
locked backside-down and artwork-face-up.

Designer full-parent polygons used for hover/hit testing are retained only as
debug metadata. Fabrication always uses the clipped visible polygon. Finished
artwork dimensions are authoritative and are never scaled.

The fixed Designer grout gap is 1.8 mm for S/M/L. The known V4 Rounded tile
relief is 1.6 mm of straight side plus a 0.8 mm rounded crown, for 2.4 mm above
the flat grout surface. The repository does **not** contain authoritative base
or Grout/Thinset body thicknesses, nor a historical crown tessellation value.
Consequently base and grout thickness are mandatory profile inputs; Phase 1
does not silently establish product values. Crown segment count is likewise
profile-versioned. A fixture profile used by tests is validation data, not a
shipping fabrication specification.

The Phase 1 body stack is support-free:

1. Base: rectangular slab from Z=0 to the configured base top.
2. Grout/Thinset: aligned rectangular slab from the base top to grout top.
3. Tiles: resolved visible polygons beginning at grout top, with 1.6 mm
   straight sides and a deterministic quarter-round 0.8 mm crown.

This creates exact, non-overlapping material interfaces while grout rises to
the tile sides. The grout surface generator is isolated so a physically
validated shallow concavity can replace the current flat surface later.

## Logical channels

Logical channels are project-global and remain separate even when a user later
maps them to the same filament:

- `base`
- `grout-thinset`
- `tile-color-1` through `tile-color-4`

Only used tile-color bodies are generated. Color-channel identity is global;
border tiles retain border ownership/role metadata but use ordinary tile
geometry and an ordinary project tile-color channel. Bodies share one origin
and are not boolean-unioned into an anonymous mesh. Bambu Studio will own the
eventual mapping from logical bodies to real AMS/material slots. STL export
must never be blocked merely because hardware mapping requires swaps or more
than one AMS; Phase 1's four project tile channels reflect the current product
contract.

## Frame land

The fabrication profile records the fixed future frame-land default of 3/8 in
(9.525 mm). It lies outside the Designer artwork boundary and must never steal
artwork area. Phase 1 does not generate it; this keeps the single-panel proof
focused on Base, Grout/Thinset, and Tile bodies while preserving a deterministic
place for it in the resolved fabrication contract.

## Deferred panelization and P1S profile

Phase 1 emits exactly one monolithic panel and performs no scaling. Automatic
P1S panelization will later maximize useful area and minimize plate count using
variable panel sizes bounded by grout lines. Exactly one physical panel will
occupy each plate. The usable envelope will be the validated P1S build area
minus prime-tower and clearance reservations; no arbitrary reservation is
encoded yet.

Panel seams will use simple horizontal/vertical grout lines and initially a
split-grout seam that reconstructs the normal 1.8 mm channel. Visible seam
registration is independent from connector clearance.

Future connectors are top-down, puzzle-like, deterministic, keyed against
wrong neighbors/orientation, support-free where practical, and lightly snug.
They provide alignment and temporary retention only; ACP, adhesive, and the
traditional frame provide long-term integrity. A few medium connectors are
preferred over many fragile features. Local base reinforcement is permissible
after physical validation.

Panels will receive deterministic assembly IDs (`A1`, `A2`, ...), remain
independently reprintable, and may later receive first-layer-safe underside
identification only if bed adhesion is not compromised. The optional assembly
map will derive from the same resolved model and show IDs, positions,
orientation, mating relationships, dimensions, frame land, and useful keys.

## Export contracts

Separate-body STL uses a common origin, for example `A1_Base.stl`,
`A1_GroutThinset.stl`, and one STL for each used global Tile Color. Importing
them together reconstructs the panel with no repositioning. STL carries no AMS
semantics.

The future editable SVG is unpanelized artwork, with semantic Border, Grout,
and Tile Color groups while retaining individual addressable tile objects.
Manufacturing seams are excluded unless a future manufacturing SVG explicitly
requests them.

The preferred future 3MF maps one physical panel to one Bambu plate and keeps
named Base, Grout/Thinset, and used global Tile Color bodies. Mosaica will ship
a physically validated P1S process profile governing layers, walls, shells,
infill, top pattern, ironing, and speeds. Those are product-process settings,
not ordinary Designer controls. No slicer invocation or final profile is part
of Phase 1.

## Explicitly deferred after Phase 1

Automatic panelization, a validated safe P1S envelope, prime-tower placement,
frame-land solids, seams, keyed connectors and calibration, reinforcement,
underside IDs, assembly maps, multi-plate 3MF, AMS UI, slicer control, finished
P1S process/ironing settings, concave grout, incremental regeneration, custom
panel layouts, and user fabrication controls remain Phase 2 or later work.

## Phase 1.1 physical-review fixture

Regenerate the compact, recognizable three-color review mosaic with:

```bash
python -m mosaica.fabricate.review --out fabricate_phase1_1_review
```

The command resolves a canonical Designer fixture through the same immutable
Fabricate model, builds one panel, and writes aligned `Review_Base.stl`,
`Review_GroutThinset.stl`, used `Review_TileColor*.stl` bodies, and
`manifest.json`. The manifest marks the 2.0 mm Base and 1.0 mm Grout/Thinset
split `fixture_only`; it does not establish those values as product
specifications. It includes shared-coordinate validation, body bounds, mesh
statistics, byte hashes, and a deterministic geometry signature.

No review 3MF is generated in Phase 1.1. The repository has no 3MF packaging
support, and adding it solely for review would create a second export
architecture before the final Bambu/P1S contract is implemented.

### Crown-coordinate invariant

Crown inset rings must remain in the resolved panel coordinate system. The
standalone print-part offset helper intentionally normalizes polygons to a
local origin for geometry deduplication and therefore must not be used for
panel crown construction. Fabricate uses an origin-preserving convex inset.
Review validation checks each tile shell against its source polygon, limits
triangle edges by that polygon's diameter, verifies one component per source
tile, and parses every STL back for topology and spatial validation.

### Straight fabrication perimeter

Designer and resolved artwork dimensions continue to describe the unscaled,
unmoved pre-trim lattice rectangle. Fabricate exposes a separate manufacturing
rectangle after applying final straight cuts to complete 3D bodies. On the
staggered axis, the cut datum is derived as one half of the authoritative grout
gap (0.9 mm for 1.8 mm grout), matching the neighboring full-tile extremum.
Point Top therefore trims left/right to the half-gap datum; Flat Top applies
the rotated rule to top/bottom. The non-staggered sides remain at the artwork
planes. Base, Grout/Thinset, and crossing Tile Color solids share the final
rectangle. Interior tiles, centers, pitch, crowns, and ordinary grout channels
are unchanged.

Every tile begins from its authoritative full parent hex. The complete V4
Rounded solid is generated first and then clipped against all four fabrication
planes. Original parent-hex edges retain the factory crown; every artificial
artwork/manufacturing edge is a capped, vertical, full-height straight cut,
independent of orientation or piece classification.

Future frame land must grow outward from this clean fabrication perimeter,
not from the alternating pre-trim tile/grout extremities. For the Phase 1.1
Point Top fixture, artwork remains 130.8 mm wide while fabrication spans X
0.9–129.9 mm and is therefore 129.0 mm wide. No scaling or compensating
lattice translation is performed.

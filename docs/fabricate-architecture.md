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

The fixed Designer grout gap is 1.8 mm for S/M/L. The current production V4
Rounded tile relief is 1.3 mm of straight side plus the frozen 0.8 mm rounded
crown, for 2.1 mm above the grout datum. Base, grout, relief, and crown segment
count remain explicit versioned profile inputs. Historical fixture profiles
used by tests are validation data, not competing shipping specifications.

The Phase 1 body stack is support-free:

1. Base: rectangular slab from Z=0 to the configured base top.
2. Grout/Thinset: aligned rectangular slab from the base top to grout top.
3. Tiles: resolved visible polygons beginning at grout top, with 1.3 mm
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

Panel seams will follow the grout network and reconstruct the normal 1.8 mm
channel without cutting tiles. The natural complementary boundary is evaluated
first; hidden registration or retention geometry is deferred unless physical
testing proves a concrete need. ACP, adhesive, and the traditional frame
provide long-term integrity. Local base reinforcement remains permissible only
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

The checked-in V4 Rounded physical-validation baseline is preserved at
`archive/veradura/viability/fabricate_phase1_1_review/`. The command above
creates a fresh local review output at the repository root; that reproducible
directory is intentionally ignored.

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

## Phase 2A concave-grout and connection prototype

Phase 2A adds an optional `concave` fabrication-profile grout surface while
leaving the validated `flat` surface unchanged. The experiment uses a smooth
0.30 mm maximum depression below the existing grout-top datum. The 1.8 mm
visible grout gap, 2.0 mm Base, 1.0 mm structural Grout/Thinset body, and V4
Rounded tile geometry remain fixed. Each authoritative tile edge now anchors a
boundary-driven strip with three deterministic 0.30 mm transverse intervals per
half-gap. This preserves the smooth sin-squared profile without a directional
Cartesian heightfield approximating diagonal boundaries. The depression and
edge-driven tessellation remain experimental physical-test values, not
production specifications.

Generate the dedicated two-panel fixture with:

```bash
python -m mosaica.fabricate.phase2a --out fabricate_phase2a_review
```

Its vertical, zig-zagging seam follows the center of the existing parent-hex
grout network. Tile ownership changes at that seam, but no tile is divided or
re-shaped. Phase 2A intentionally has no dedicated connector, locator,
clearance, or retention geometry. The long complementary grout-line boundary
itself is the registration experiment. Phase 2A physical review confirmed that
it aligns well without connectors while both flat panel backs rest on the same
backer.

Visible tile and grout alignment is authoritative. The eventual ACP backer,
adhesive, and frame provide permanent structural integrity. No dedicated
connector is planned for the current release. This manually controlled split
is not automatic production panelization, nesting, or a final frame-land
design.

## Phase 2B physical decision and production baseline

The original validated baseline used a 2.0 mm Base, 1.0 mm Grout/Thinset layer,
1.6 mm straight wall, and 0.8 mm crown for 5.4 mm total Z. It remains recorded
as legacy physical provenance. Subsequent physical review approved the thinner
stack as the single production default: 1.5 mm Base, 1.0 mm Grout/Thinset,
1.3 mm straight wall, and 0.8 mm crown for 2.1 mm tile relief and 4.6 mm total
Z.

The physically approved V4 crown is frozen exactly, including its 0.8 mm
height, inset, six-segment curvature, XY coordinates, and clipped-edge behavior.
The corrected orientation-independent 0.30 mm concave grout is retained.

Panels use spreadsheet-style identities: alphabetic rows from top to bottom and
numeric columns from left to right (`A1`, `A2`, `B1`, and so on). Export object
names, filenames, manifest records, and physical marks share that identity.
This model is sufficient for a future assembly map without assuming a single
row. The current fixture uses `A1` and `A2`.

The dependency-free dot-matrix panel ID was physically approved. Each Base
contains only its normally ordered, backside-readable panel ID using 1.0 mm
cells debossed 0.35 mm. There is no TOP arrow, mirrored text, wordmark, or
decorative content. The cavities remain within the 1.5 mm Base, leave 1.15 mm
above their deepest surface, remain clear of the perimeter and natural seam,
and are nonstructural.

Generate the comparison package with:

```bash
python -m mosaica.fabricate.phase2b --out fabricate_production_review
```

The current intended P1S production direction is Bambu's 0.20 mm Standard
profile with two wall loops and Adaptive Variable Layer Height on. Standard/no
ironing is the default export surface finish. Ironing is optional; the
validated premium choice irons Topmost surfaces using Concentric pattern, 18%
flow, 30 mm/s speed, and 0.15 mm line spacing. These are process metadata, not
modeled geometry. Future production 3MF and assembly-map systems may consume
them; this phase does not implement either system.

## Phase 3A automatic grout-line panelization

Mosaica v1 uses a fixed **210 × 210 mm safe fabrication envelope** for P1S
panel output. This is an intentionally conservative Mosaica production
constraint, not the printer's nominal build volume. It reserves practical
space for the prime tower and normal slicer margins. One finished mosaic panel
is intended to become one future build plate.

Automatic panelization begins with `ceil(fabricated width / 210)` columns and
`ceil(fabricated height / 210)` rows. Ideal evenly spaced divisions are targets
only. Candidate cuts are selected from shared expanded-parent-cell boundaries,
so each seam follows the existing grout network and every tile remains whole.
Actual irregular cell-union bounds—not nominal grid-cell dimensions—must fit
the safe envelope.

The deterministic optimization order is:

1. minimum total panel count;
2. minimum worst normalized deviation from mean panel area;
3. fewest seam direction changes, then seam vertices and total length;
4. least deviation from ideal divisions and stable row/column order.

Nearby alternate seams are evaluated before panel count increases. If the
theoretical layout cannot fit, plausible row/column factorizations are tested
in increasing total-panel order. Every accepted logical grid cell must contain
one contiguous, hole-free parent-cell region; islands, wrapped ownership, empty
micro-panels, tile cuts, and connector geometry are rejected.

Panel IDs retain spreadsheet order (`A1`, `A2`, `B1`, and so on) and drive the
approved 1.0 mm-cell, 0.35 mm-deep backside mark. Manifest neighbors explicitly
record top/bottom/left/right assembly relationships. A per-panel print rotation
field supports 0° or future 90° plate placement without changing artwork-space
orientation or panel identity. With the current square envelope, rotation does
not change fit eligibility.

Panel component STLs remain in shared global artwork coordinates within each
panel directory. Loading one directory's Base, Grout/Thinset, and used Tile
Color bodies as a multipart object therefore preserves alignment. The manifest
also retains bounds, row/column identity, neighbors, seams, tile ownership,
orientation, and signatures needed for a later assembly map without rerunning
panelization.

Run saved MosaicProject panelization with:

```bash
python -m mosaica.fabricate.panelize --project PROJECT.json --out fabricate_panelized_review
```

Phase 3A itself does not generate 3MF files or slicer plates. Phase 3C resolves
the fabrication mode before invoking the unchanged natural-seam panelizer.
Fast supplies a 228 × 228 mm envelope; Museum supplies 210 × 210 mm. The mode
may therefore change the panel grid, seams, IDs, and neighbors without changing
tile geometry or the optimizer's priorities.

## Approved 4.6 mm production stack

Physical review approved the 1.5 mm Base and 1.3 mm straight wall while
retaining the 1.0 mm Grout/Thinset layer and geometrically identical 0.8 mm
crown. The resulting 4.6 mm stack is the production default. The former 5.4 mm
stack remains only as explicit legacy provenance and regression evidence.

The production nominal grout datum is Z 2.5 mm, its unchanged 0.30 mm
concave trough reaches Z 2.2 mm, the straight wall ends at Z 3.8 mm, and the
crown ends at Z 4.6 mm. The approved 1.0 mm-cell backside panel ID remains
0.35 mm deep, leaving 1.15 mm of Base above its cavity. Natural seams, whole
tiles, clipped geometry, grout width, panelization, and connector-free assembly
are unchanged.

Generate the compact A1/A2 production fixture with:

```bash
python -m mosaica.fabricate.phase2b --out fabricate_production_review
```

Slicer-setting injection remains deferred.

## Phase 3B production 3MF and P1S plate planning

Phase 3B writes one standards-compliant multipart 3MF file per physical panel,
plus a project-level JSON manifest. Files use deterministic identities such as
`Mosaica_A1.3mf`; each contains one build item named `Panel A1`, composed from
separate Base, Grout-Thinset, and used Tile Color mesh objects. Unused Tile
Color channels are omitted. The logical bodies remain independent even when a
user later assigns several of them to one physical filament.

This one-file-per-panel strategy is the deliberate v1 fallback because the
repository does not have a documented, stable contract for Bambu's proprietary
multi-plate project metadata. The files contain standard 3MF Core model,
component, Base Materials, build-item, and affine-transform structures. No
Bambu-specific extension is claimed or synthesized. Bambu Studio owns the
user's AMS/filament mapping and may map six logical channels across more than
one AMS or map several logical channels to one filament.

Every panel retains global artwork-space mesh coordinates and logical bounds.
Bambu Studio does not preserve Mosaica's requested plate location when it
imports a non-Bambu 3MF, so the export no longer promises automatic X/Y
placement. The user positions each imported panel in Bambu Studio. Print
rotation metadata accepts 0° or 90° without changing logical panel identity or
backside-mark semantics.

Phase 3C defines two fabrication modes from one authoritative mode profile:

- **Fast** uses the physically validated 228 × 228 mm envelope, disables the
  Prime Tower, requires **Others → Brim type → No Brim**, leaves ironing off,
  and enables Adaptive Variable Layer Height. It targets good finished quality
  with fewer/larger panels while accepting a small risk of minor color transfer.
- **Museum** uses the 210 × 210 mm envelope, enables the Prime Tower, leaves
  brim behavior at the Bambu default, enables Adaptive Variable Layer Height,
  and uses Topmost surfaces / Concentric ironing at 18% flow, 30 mm/s, and
  0.15 mm spacing. It prioritizes maximum finish quality and color purity.

These settings are structured process intent in `manifest.json`; they are not
embedded as undocumented slicer state. Museum intentionally contains no Prime
Tower brim-width recommendation.

Generate a saved-project package with:

```bash
python -m mosaica.fabricate.three_mf \
  --project PROJECT.json \
  --out fabricate_3mf_export \
  --mode fast
```

Use `--mode museum` for the premium workflow. The former `--surface-finish`
option remains a deprecated CLI/API bridge (`standard` maps to Fast; `ironed`
maps to Museum) so the mode still resolves before panelization.

The manifest records the panel grid, identities, neighbors, logical and plate
coordinates, channel ordering, physical profile, process intent, SHA-256
signatures, zero tile cuts, no connectors, and round-trip validation. Print
time and filament estimates remain unavailable until an actual slicer provides
them. Future UI integration can expose export and logical-to-physical filament
mapping without changing generation or panelization.

Bambu Studio compatibility requires the OPC package documents to serialize
their Content Types and Relationships namespaces as default namespaces.
Namespace-equivalent `ns0:Types` and `ns0:Relationships` output was rejected by
Bambu Studio as containing no geometry; the corresponding default-namespace
form loaded successfully. This requirement was isolated with a controlled
Bambu-generated R1/R5 comparison and is enforced with raw-byte regression
tests. Mosaica-specific process and panel metadata remains in `manifest.json`
rather than undeclared vendor-prefixed Core model metadata.

Bambu Studio currently warns that Mosaica's 3MF is not from Bambu Lab and will
load geometry and color data only. That warning is expected: reliable multipart
geometry/color import is the current compatibility target, not proprietary
Bambu project persistence. A later mode-specific Print Guide PDF will consume
the same structured mode definitions and manifest instructions.

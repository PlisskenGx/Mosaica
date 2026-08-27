# Mosaica project files

A `.mosaica` file is the editable source project. Fabrication packages remain
derived output and are never embedded in the project container.

Schema version 2 is a deterministic ZIP container containing `project.json`
and, when artwork is present, a content-addressed SVG member such as
`artwork/artwork-0123456789abcdef.svg`. `project.json` records the independent
project schema version and application version, setup inputs, palette and role
mappings, grout and border state, artwork metadata and transforms, generated
tile assignments, manual paint overrides, and artwork edit state. Physical
geometry is regenerated from the saved tile/canvas setup; generated artwork
assignments and manual overrides are authoritative and round-trip exactly.

Schema v2 setup state explicitly records `tile_family`, `tile_preset`, and
`tile_orientation`. The only currently supported production family is
`hexagon`, with `point_top` and `flat_top` orientations and the existing S/M/L
presets. The internal geometry shape ID remains `hex`; it is not serialized as
the production family identity.

Schema-v1 projects remain readable. Because v1 implicitly meant Hexagon, the
loader migrates them in memory to `tile_family = "hexagon"` while preserving
their preset, orientation, artwork, Border, palette, and paint state. Opening
does not rewrite or mark a project edited; its next explicit Save or Save As
writes schema v2. Missing or unknown v2 families and invalid family-specific
orientation/preset combinations are rejected. Schemas newer than this version
are rejected rather than guessed.

Artwork members are embedded so reopening never requires the original upload.
The current browser upload supplies the original filename but not a trustworthy
absolute source path, so the project records the filename and content identity
without inventing filesystem provenance.

The loader reads members directly without extracting them. It rejects absolute
or traversing paths, duplicate members, oversized containers, malformed ZIP or
JSON data, unsupported schemas, and missing or damaged referenced artwork.
Saving writes and validates a temporary sibling archive before atomically
replacing the destination.

`preview.png` is intentionally deferred. The container structure permits adding
it later without changing the authoritative schema contract.

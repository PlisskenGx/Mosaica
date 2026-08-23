# Mosaica project files

A `.mosaica` file is the editable source project. Fabrication packages remain
derived output and are never embedded in the project container.

Schema version 1 is a deterministic ZIP container containing `project.json`
and, when artwork is present, a content-addressed SVG member such as
`artwork/artwork-0123456789abcdef.svg`. `project.json` records the independent
project schema version and application version, setup inputs, palette and role
mappings, grout and border state, artwork metadata and transforms, generated
tile assignments, manual paint overrides, and artwork edit state. Physical
geometry is regenerated from the saved tile/canvas setup; generated artwork
assignments and manual overrides are authoritative and round-trip exactly.

Artwork members are embedded so reopening never requires the original upload.
The current browser upload supplies the original filename but not a trustworthy
absolute source path, so schema v1 records the filename and content identity
without inventing filesystem provenance.

The loader reads members directly without extracting them. It rejects absolute
or traversing paths, duplicate members, oversized containers, malformed ZIP or
JSON data, unsupported schemas, and missing or damaged referenced artwork.
Saving writes and validates a temporary sibling archive before atomically
replacing the destination.

`preview.png` is intentionally deferred. The container structure permits adding
it later without changing the authoritative schema contract.

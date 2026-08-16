from __future__ import annotations

import json
from pathlib import Path

from .mesh import MeshBody, SinglePanelGeometry, Triangle3, triangle_normal


def write_mesh_stl(body: MeshBody, path: str | Path) -> Path:
    path = Path(path)
    lines = [f"solid {body.body_id}"]
    for triangle in body.triangles:
        normal = triangle_normal(triangle)
        lines.append("  facet normal " + " ".join(f"{value:.9g}" for value in normal))
        lines.append("    outer loop")
        for vertex in triangle:
            lines.append("      vertex " + " ".join(f"{value:.9f}" for value in vertex))
        lines.extend(("    endloop", "  endfacet"))
    lines.append(f"endsolid {body.body_id}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def parse_ascii_stl(path: str | Path) -> tuple[Triangle3, ...]:
    """Parse Mosaica's deterministic ASCII STL for round-trip validation."""

    vertices = []
    triangles = []
    for line in Path(path).read_text(encoding="ascii").splitlines():
        values = line.strip().split()
        if values[:1] != ["vertex"]:
            continue
        if len(values) != 4:
            raise ValueError("STL vertex record must contain three coordinates.")
        vertices.append(tuple(float(value) for value in values[1:]))
        if len(vertices) == 3:
            triangles.append(tuple(vertices))
            vertices = []
    if vertices:
        raise ValueError("STL contains an incomplete triangle.")
    if not triangles:
        raise ValueError("STL contains no triangle geometry.")
    return tuple(triangles)  # type: ignore[return-value]


def export_single_panel_prototype(
    panel: SinglePanelGeometry, output_directory: str | Path,
) -> dict[str, Path]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    paths = {}
    body_records = []
    for body in panel.bodies:
        filename = f"{body.body_id}.stl"
        paths[body.material_channel_id] = write_mesh_stl(body, output / filename)
        body_records.append({
            "body_id": body.body_id,
            "name": body.name,
            "material_channel_id": body.material_channel_id,
            "filename": filename,
            "tile_ids": list(body.tile_ids),
            "bounds_mm": list(body.bounds_mm),
        })
    manifest = output / "fabricate_phase1_manifest.json"
    manifest.write_text(json.dumps({
        "schema": {"name": "mosaica-fabricate-panel", "version": 1},
        "panel_id": panel.panel_id,
        "fabrication_bounds_mm": list(panel.fabrication_bounds_mm),
        "resolved_model": panel.model.to_dict(),
        "bodies": body_records,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths["manifest"] = manifest
    return paths

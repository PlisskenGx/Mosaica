from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from math import ceil
from pathlib import Path

from .. import __version__
from .export import parse_ascii_stl, write_mesh_stl
from .mesh import (
    MeshBody, SinglePanelGeometry, clip_mesh_to_fabrication_perimeter,
    concave_grout_mesh, concave_grout_spatial_validation,
    fabrication_perimeter_bounds, mesh_validation,
    rounded_tile_mesh, x_monotone_heightfield_mesh,
)
from .model import FabricationProfile, Point2MM, ResolvedFabricationModel
from .review import build_review_model


PHASE2A_ARTIFACT_NAME = "Mosaica Fabricate Phase 2A Review"
PHASE2A_PROFILE = FabricationProfile(
    profile_id="phase2a-concave-grout-natural-seam-prototype",
    version=1,
    base_thickness_mm=2.0,
    grout_thickness_mm=1.0,
    straight_tile_relief_mm=1.6,
    rounded_crown_mm=0.8,
    crown_segments=6,
    grout_surface="concave",
    grout_depression_mm=0.30,
    grout_mesh_step_mm=0.30,
    frame_land_mm=0.0,
)
@dataclass(frozen=True)
class SeamPath:
    orientation: str
    points_mm: tuple[Point2MM, ...]

    def x_at(self, y_mm: float) -> float:
        for first, second in zip(self.points_mm, self.points_mm[1:]):
            low, high = sorted((first[1], second[1]))
            if low - 1e-8 <= y_mm <= high + 1e-8:
                if abs(second[1] - first[1]) <= 1e-12:
                    continue
                scale = (y_mm - first[1]) / (second[1] - first[1])
                return round(first[0] + scale * (second[0] - first[0]), 9)
        raise ValueError(f"Y={y_mm} lies outside the grout-line seam.")


@dataclass(frozen=True)
class Phase2APrototype:
    model: ResolvedFabricationModel
    seam: SeamPath
    panels: tuple[SinglePanelGeometry, SinglePanelGeometry]
    tile_ownership: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Phase2AReviewPackage:
    output_directory: Path
    manifest_path: Path
    stl_paths: tuple[Path, ...]
    geometry_signature: str


def build_phase2a_model() -> ResolvedFabricationModel:
    return replace(build_review_model(), profile=PHASE2A_PROFILE)


def _expanded_parent_cell(model: ResolvedFabricationModel, tile) -> tuple[Point2MM, ...]:
    ratio = (model.tile_flat_to_flat_mm + model.grout_gap_mm) / model.tile_flat_to_flat_mm
    center_x, center_y = tile.center_mm
    return tuple((
        round(center_x + (x - center_x) * ratio, 6),
        round(center_y + (y - center_y) * ratio, 6),
    ) for x, y in tile.full_polygon_mm)


def _edge_key(first: Point2MM, second: Point2MM) -> tuple[Point2MM, Point2MM]:
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def derive_vertical_grout_seam(
    model: ResolvedFabricationModel,
) -> tuple[SeamPath, dict[str, str]]:
    """Derive the parent-cell boundary separating two whole-tile ownership sets."""

    midpoint = model.artwork_width_mm / 2.0
    ownership = {
        tile.tile_id: ("A" if tile.center_mm[0] <= midpoint + 1e-8 else "B")
        for tile in model.tiles
    }
    edge_owners: dict[tuple[Point2MM, Point2MM], list[str]] = {}
    for tile in model.tiles:
        polygon = _expanded_parent_cell(model, tile)
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            edge_owners.setdefault(_edge_key(first, second), []).append(tile.tile_id)
    seam_edges = [
        edge for edge, tile_ids in edge_owners.items()
        if len(tile_ids) == 2 and ownership[tile_ids[0]] != ownership[tile_ids[1]]
    ]
    adjacency: dict[Point2MM, list[Point2MM]] = {}
    for first, second in seam_edges:
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    endpoints = sorted(
        (point for point, neighbors in adjacency.items() if len(neighbors) == 1),
        key=lambda point: point[1],
    )
    if len(endpoints) != 2:
        raise ValueError("Whole-tile ownership did not produce one continuous seam.")
    ordered = [endpoints[0]]
    previous = None
    current = endpoints[0]
    while current != endpoints[1]:
        choices = [value for value in adjacency[current] if value != previous]
        if len(choices) != 1:
            raise ValueError("Grout seam branches and is unsuitable for Phase 2A.")
        previous, current = current, choices[0]
        ordered.append(current)
    ordered.sort(key=lambda point: point[1])
    _, top, _, bottom = fabrication_perimeter_bounds(model)
    untrimmed = SeamPath("vertical-grout-line", tuple(ordered))
    clipped = [(untrimmed.x_at(top), top)]
    clipped.extend(point for point in ordered if top < point[1] < bottom)
    clipped.append((untrimmed.x_at(bottom), bottom))
    return SeamPath("vertical-grout-line", tuple(clipped)), ownership


def _y_rows(
    top: float, bottom: float, step: float, seam: SeamPath,
) -> tuple[float, ...]:
    count = max(1, ceil((bottom - top) / step))
    values = {round(top + (bottom - top) * index / count, 9) for index in range(count + 1)}
    values.update(round(point[1], 9) for point in seam.points_mm)
    return tuple(sorted(value for value in values if top <= value <= bottom))


def _panel_tile_body(model, panel_id: str, channel, tiles) -> MeshBody:
    profile = model.profile
    grout_top = profile.base_thickness_mm + profile.grout_thickness_mm
    solids = tuple(
        clip_mesh_to_fabrication_perimeter(
            rounded_tile_mesh(
                tile.full_polygon_mm, grout_top,
                profile.straight_tile_relief_mm, profile.rounded_crown_mm,
                profile.crown_segments,
            ), model,
        ) for tile in tiles
    )
    return MeshBody(
        f"panel-{panel_id.lower()}-{channel.channel_id}",
        f"Panel {panel_id} {channel.name}", channel.channel_id,
        tuple(triangle for solid in solids for triangle in solid),
        tuple(tile.tile_id for tile in tiles), tuple(len(solid) for solid in solids),
    )


def build_phase2a_prototype() -> Phase2APrototype:
    model = build_phase2a_model()
    seam, ownership = derive_vertical_grout_seam(model)
    left, top, right, bottom = fabrication_perimeter_bounds(model)
    base_rows = _y_rows(top, bottom, 3.0, seam)

    panels = []
    for panel_id in ("A", "B"):
        panel_tiles = tuple(
            tile for tile in model.tiles if ownership[tile.tile_id] == panel_id
        )
        if panel_id == "A":
            base_left, base_right = (lambda _y: left), seam.x_at
        else:
            base_left, base_right = seam.x_at, (lambda _y: right)
        base_triangles = x_monotone_heightfield_mesh(
            base_rows, base_left, base_right, 0.0,
            lambda _x, _y: model.profile.base_thickness_mm, 3.0,
        )
        grout_triangles = concave_grout_mesh(model, tiles=panel_tiles)
        bodies = [
            MeshBody(f"panel-{panel_id.lower()}-base", f"Panel {panel_id} Base", "base", base_triangles),
            MeshBody(
                f"panel-{panel_id.lower()}-grout-thinset",
                f"Panel {panel_id} Grout/Thinset", "grout-thinset", grout_triangles,
            ),
        ]
        for channel in (value for value in model.channels if value.kind == "tile_color"):
            tiles = tuple(
                tile for tile in model.tiles
                if ownership[tile.tile_id] == panel_id
                and tile.material_channel_id == channel.channel_id
            )
            if tiles:
                bodies.append(_panel_tile_body(model, panel_id, channel, tiles))
        panel_bounds = (
            min(body.bounds_mm[0] for body in bodies),
            min(body.bounds_mm[1] for body in bodies),
            max(body.bounds_mm[3] for body in bodies),
            max(body.bounds_mm[4] for body in bodies),
        )
        panels.append(SinglePanelGeometry(panel_id, model, tuple(bodies), panel_bounds))
    return Phase2APrototype(
        model, seam, (panels[0], panels[1]),
        tuple(sorted(ownership.items())),
    )


def _signature(prototype: Phase2APrototype) -> str:
    payload = {
        "model": prototype.model.to_dict(),
        "seam": prototype.seam.points_mm,
        "panels": [
            [(body.body_id, body.triangles) for body in panel.bodies]
            for panel in prototype.panels
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def generate_phase2a_review_package(output_directory: str | Path) -> Phase2AReviewPackage:
    prototype = build_phase2a_prototype()
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    stl_paths = []
    records = []
    ownership = dict(prototype.tile_ownership)
    all_bodies = [
        (panel.panel_id, body) for panel in prototype.panels for body in panel.bodies
    ]
    for owner, body in all_bodies:
        filename = body.name.replace("/", "-").replace(" ", "_") + ".stl"
        path = write_mesh_stl(body, output / filename)
        parsed = MeshBody(
            body.body_id, body.name, body.material_channel_id,
            parse_ascii_stl(path), body.tile_ids, body.solid_triangle_counts,
        )
        source_validation = mesh_validation(body)
        round_trip_validation = mesh_validation(parsed)
        if not source_validation["watertight"] or not round_trip_validation["watertight"]:
            raise ValueError(f"{body.name} failed watertight STL round-trip validation.")
        stl_paths.append(path)
        record = {
            "owner": owner,
            "body_id": body.body_id,
            "logical_channel": body.material_channel_id,
            "filename": filename,
            "bounds_mm": list(body.bounds_mm),
            "tile_ids": list(body.tile_ids),
            "geometry_signature_sha256": sha256(
                json.dumps(body.triangles, separators=(",", ":")).encode()
            ).hexdigest(),
            "stl_sha256": sha256(path.read_bytes()).hexdigest(),
            "mesh_validation": source_validation,
            "stl_round_trip": {
                "valid": len(parsed.triangles) == len(body.triangles)
                and round_trip_validation["watertight"],
                "mesh_validation": round_trip_validation,
                "bounds_mm": list(parsed.bounds_mm),
            },
        }
        if body.material_channel_id == "grout-thinset":
            panel_tiles = tuple(
                tile for tile in prototype.model.tiles
                if ownership[tile.tile_id] == owner
            )
            record["concave_surface_validation"] = (
                concave_grout_spatial_validation(
                    prototype.model, body.triangles, tiles=panel_tiles,
                )
            )
        records.append(record)
    signature = _signature(prototype)
    manifest = {
        "schema": {"name": "mosaica-fabricate-phase2a-review", "version": 1},
        "artifact_name": PHASE2A_ARTIFACT_NAME,
        "application_version": __version__,
        "fabrication_profile": prototype.model.profile.__dict__,
        "grout_mode": "concave",
        "grout_depression_mm": PHASE2A_PROFILE.grout_depression_mm,
        "grout_gap_mm": prototype.model.grout_gap_mm,
        "seam": {
            "orientation": prototype.seam.orientation,
            "location_mm": [list(point) for point in prototype.seam.points_mm],
            "follows_parent_hex_grout_centerline": True,
            "tile_cuts_created": 0,
        },
        "panels": [
            {
                "panel_id": panel.panel_id,
                "bounds_mm": list(panel.fabrication_bounds_mm),
                "tile_ids": sorted(tile_id for tile_id, owner in ownership.items() if owner == panel.panel_id),
            }
            for panel in prototype.panels
        ],
        "panel_connection": {
            "type": "natural_grout_line_seam",
            "dedicated_connector_geometry": False,
            "tile_cuts_created": 0,
            "physical_test_purpose": (
                "evaluate natural seam registration before adopting any "
                "dedicated alignment or retention geometry"
            ),
            "permanent_structure": "ACP_backer_and_adhesive",
        },
        "coordinate_system": {
            "units": "mm", "origin": prototype.model.origin,
            "export": "shared global assembly coordinates",
        },
        "body_channel_ownership": records,
        "geometry_signature_sha256": signature,
        "frame_land_generated": False,
        "automatic_panelization": False,
        "permanent_structure": "ACP backer and adhesive",
        "bambu_import_instructions": [
            "Select every Panel A and Panel B STL together in Bambu Studio.",
            "Choose load as one object with multiple parts so shared global coordinates are retained.",
            "Assign Base, Grout/Thinset, and Tile Color bodies to the intended filaments.",
            "Print backside down and artwork face up using the existing validated process baseline.",
            "Place both panels on the same flat backer and align the complementary natural grout-line seam.",
            "Evaluate X/Y and rotational registration without dedicated connector geometry.",
        ],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Phase2AReviewPackage(output, manifest_path, tuple(stl_paths), signature)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic Mosaica Fabricate Phase 2A review package.",
    )
    parser.add_argument("--out", default="fabricate_phase2a_review")
    arguments = parser.parse_args(argv)
    package = generate_phase2a_review_package(arguments.out)
    print(f"Fabricate Phase 2A review: {package.output_directory}")
    print(f"Manifest: {package.manifest_path}")
    print(f"Geometry signature: {package.geometry_signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from .. import __version__
from ..designer import DesignerProjectShell
from .export import parse_ascii_stl, write_mesh_stl
from .mesh import (
    MeshBody, SinglePanelGeometry, build_single_panel_geometry,
    maximum_triangle_edge, mesh_validation, tile_body_spatial_validation,
)
from .model import FabricationProfile, ResolvedFabricationModel
from .resolve import resolve_designer_project


REVIEW_ARTIFACT_NAME = "Mosaica Fabricate Phase 1.1 Review"
REVIEW_PROFILE = FabricationProfile(
    profile_id="phase1.1-physical-review-fixture",
    version=1,
    base_thickness_mm=2.0,
    grout_thickness_mm=1.0,
    crown_segments=6,
)


@dataclass(frozen=True)
class ReviewPackage:
    output_directory: Path
    manifest_path: Path
    stl_paths: tuple[Path, ...]
    geometry_signature: str


def _review_fixture() -> tuple[DesignerProjectShell, dict[str, str]]:
    """Return a compact, recognizable three-color V design."""

    shell = DesignerProjectShell.create_custom("s", "point_top", 5, 5)
    black = "project-color-2"
    clay = "project-color-4"
    overrides: dict[str, str] = {}
    for index, placement in enumerate(shell.geometry.placements):
        if not placement.principal_grid:
            continue
        row = placement.principal_row
        column = placement.principal_column
        if row is None or column is None:
            continue
        # Two descending strokes form a simple Veradura-relevant V mark.
        if (row, column) in {
            (0, 0), (0, 4), (1, 0), (1, 4),
            (2, 1), (2, 3), (3, 1), (3, 3), (4, 2),
        }:
            overrides[f"placement-{index:06d}"] = black
        elif (row, column) == (2, 2):
            overrides[f"placement-{index:06d}"] = clay
    return shell, overrides


def build_review_model() -> ResolvedFabricationModel:
    shell, paint_overrides = _review_fixture()
    return resolve_designer_project(
        shell, REVIEW_PROFILE, paint_overrides=paint_overrides,
    )


def build_review_panel() -> SinglePanelGeometry:
    return build_single_panel_geometry(build_review_model())


def _body_signature(body: MeshBody) -> str:
    payload = json.dumps(body.triangles, separators=(",", ":"))
    return sha256(payload.encode("ascii")).hexdigest()


def _geometry_signature(panel: SinglePanelGeometry) -> str:
    payload = json.dumps({
        "model": panel.model.to_dict(),
        "bodies": [
            {
                "channel": value.material_channel_id,
                "signature": _body_signature(value),
            }
            for value in panel.bodies
        ],
    }, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("ascii")).hexdigest()


def _bounds_close(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return len(first) == len(second) and all(
        abs(left - right) <= 1e-8 for left, right in zip(first, second)
    )


def validate_shared_reference_frame(panel: SinglePanelGeometry) -> dict:
    model = panel.model
    profile = model.profile
    base = panel.body("base")
    grout = panel.body("grout-thinset")
    expected_xy = panel.fabrication_bounds_mm
    errors = []
    if not _bounds_close(
        (base.bounds_mm[0], base.bounds_mm[1], base.bounds_mm[3], base.bounds_mm[4]),
        expected_xy,
    ):
        errors.append("Base does not use the complete fabrication XY frame.")
    if not _bounds_close(
        (grout.bounds_mm[0], grout.bounds_mm[1], grout.bounds_mm[3], grout.bounds_mm[4]),
        expected_xy,
    ):
        errors.append("Grout/Thinset does not align to the Base XY frame.")
    if base.bounds_mm[2] != 0.0:
        errors.append("Base backside is not on Z=0.")
    if grout.bounds_mm[2] != profile.base_thickness_mm:
        errors.append("Grout/Thinset does not begin at the Base top.")
    grout_top = profile.base_thickness_mm + profile.grout_thickness_mm
    for body in panel.bodies[2:]:
        bounds = body.bounds_mm
        if bounds[2] != grout_top:
            errors.append(f"{body.name} does not begin at the grout plane.")
        if not (
            0.0 <= bounds[0] <= bounds[3] <= model.artwork_width_mm
            and 0.0 <= bounds[1] <= bounds[4] <= model.artwork_height_mm
        ):
            errors.append(f"{body.name} leaves the fabrication XY frame.")
    return {
        "valid": not errors,
        "origin_mm": [0.0, 0.0, 0.0],
        "fabrication_bounds_mm": list(expected_xy),
        "coordinate_system": {
            "origin": model.origin,
            "axes": list(model.axes),
            "units": model.units,
        },
        "errors": errors,
    }


def export_review_package(
    panel: SinglePanelGeometry,
    output_directory: str | Path,
) -> ReviewPackage:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    names = {
        "base": "Review_Base.stl",
        "grout-thinset": "Review_GroutThinset.stl",
    }
    stl_paths = []
    records = []
    for body in panel.bodies:
        filename = names.get(
            body.material_channel_id,
            "Review_" + body.material_channel_id.title().replace("-", "") + ".stl",
        )
        path = write_mesh_stl(body, output / filename)
        stl_paths.append(path)
        stl_hash = sha256(path.read_bytes()).hexdigest()
        validation = mesh_validation(body)
        parsed_triangles = parse_ascii_stl(path)
        parsed_body = MeshBody(
            body.body_id, body.name, body.material_channel_id,
            parsed_triangles, body.tile_ids, body.solid_triangle_counts,
        )
        parsed_validation = mesh_validation(parsed_body)
        round_trip = {
            "valid": (
                len(parsed_triangles) == len(body.triangles)
                and _bounds_close(parsed_body.bounds_mm, body.bounds_mm)
                and parsed_validation["watertight"]
            ),
            "face_count": len(parsed_triangles),
            "bounds_mm": list(parsed_body.bounds_mm),
            "maximum_triangle_edge_mm": maximum_triangle_edge(parsed_triangles),
            "mesh_validation": parsed_validation,
        }
        spatial_validation = None
        if body.material_channel_id.startswith("tile-color-"):
            source_tiles = tuple(
                value for value in panel.model.tiles
                if value.material_channel_id == body.material_channel_id
            )
            grout_top = (
                panel.model.profile.base_thickness_mm
                + panel.model.profile.grout_thickness_mm
            )
            spatial_validation = tile_body_spatial_validation(
                body, source_tiles, grout_top,
                panel.model.profile.total_tile_relief_mm,
            )
            parsed_spatial = tile_body_spatial_validation(
                parsed_body, source_tiles, grout_top,
                panel.model.profile.total_tile_relief_mm,
            )
            round_trip["spatial_validation"] = parsed_spatial
            round_trip["valid"] = (
                round_trip["valid"]
                and spatial_validation["valid"]
                and parsed_spatial["valid"]
            )
        if not round_trip["valid"]:
            raise ValueError(f"{body.name} failed STL round-trip validation.")
        records.append({
            "body_id": body.body_id,
            "name": body.name,
            "logical_channel": body.material_channel_id,
            "stl_filename": filename,
            "bounds_mm": list(body.bounds_mm),
            "tile_ids": list(body.tile_ids),
            "geometry_signature_sha256": _body_signature(body),
            "stl_sha256": stl_hash,
            "mesh_validation": validation,
            "spatial_validation": spatial_validation,
            "stl_round_trip": round_trip,
        })
    reference_frame = validate_shared_reference_frame(panel)
    if not reference_frame["valid"]:
        raise ValueError("Review bodies do not share a valid reference frame: " + "; ".join(reference_frame["errors"]))
    model = panel.model
    profile = model.profile
    clipped_count = sum(value.piece_type != "full" for value in model.tiles)
    full_count = len(model.tiles) - clipped_count
    channels = [
        {
            "channel_id": value.channel_id,
            "name": value.name,
            "kind": value.kind,
            "display_color": value.display_color,
            "source_color_id": value.source_color_id,
        }
        for value in model.channels
    ]
    signature = _geometry_signature(panel)
    manifest_data = {
        "schema": {"name": "mosaica-fabricate-review", "version": 1},
        "artifact_name": REVIEW_ARTIFACT_NAME,
        "application_version": __version__,
        "fabricate_profile": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "base_thickness_mm": profile.base_thickness_mm,
            "base_thickness_status": "fixture_only",
            "grout_thinset_thickness_mm": profile.grout_thickness_mm,
            "grout_thinset_thickness_status": "fixture_only",
            "straight_tile_relief_mm": profile.straight_tile_relief_mm,
            "rounded_crown_mm": profile.rounded_crown_mm,
            "total_tile_relief_above_grout_mm": profile.total_tile_relief_mm,
            "crown_segments": profile.crown_segments,
            "grout_surface": profile.grout_surface,
        },
        "coordinate_system": reference_frame["coordinate_system"],
        "shared_reference_frame": reference_frame,
        "print_orientation": model.print_orientation,
        "units": model.units,
        "artwork_width_mm": model.artwork_width_mm,
        "artwork_height_mm": model.artwork_height_mm,
        "fabrication_bounds_mm": list(panel.fabrication_bounds_mm),
        "fabricated_width_mm": (
            panel.fabrication_bounds_mm[2] - panel.fabrication_bounds_mm[0]
        ),
        "fabricated_height_mm": (
            panel.fabrication_bounds_mm[3] - panel.fabrication_bounds_mm[1]
        ),
        "total_z_height_mm": model.physical_bounds_mm[5],
        "grout_gap_mm": model.grout_gap_mm,
        "perimeter_correction_mm": model.grout_gap_mm / 2.0,
        "dimension_semantics": {
            "designer_artwork_bounds": "unchanged_pre_trim_lattice_rectangle",
            "fabrication_bounds": "straightened_manufacturing_perimeter",
            "lattice_scaled_or_repositioned": False,
        },
        "tile_preset": model.tile_preset_id,
        "tile_flat_to_flat_mm": model.tile_flat_to_flat_mm,
        "tile_orientation": model.tile_orientation,
        "tile_profile": model.tile_profile,
        "resolved_tile_count": len(model.tiles),
        "full_tile_count": full_count,
        "clipped_tile_count": clipped_count,
        "used_logical_channels": channels,
        "geometry_signature_sha256": signature,
        "bodies": records,
        "frame_land_generated": False,
        "panelization": "not_implemented",
    }
    manifest = output / "manifest.json"
    manifest.write_text(
        json.dumps(manifest_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ReviewPackage(output, manifest, tuple(stl_paths), signature)


def generate_review_package(output_directory: str | Path) -> ReviewPackage:
    return export_review_package(build_review_panel(), output_directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic Mosaica Fabricate Phase 1.1 review package.",
    )
    parser.add_argument(
        "--out", default="fabricate_phase1_1_review",
        help="review output directory (default: fabricate_phase1_1_review)",
    )
    arguments = parser.parse_args(argv)
    package = generate_review_package(arguments.out)
    print(f"Fabricate Phase 1.1 review: {package.output_directory}")
    print(f"Manifest: {package.manifest_path}")
    print(f"Geometry signature: {package.geometry_signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

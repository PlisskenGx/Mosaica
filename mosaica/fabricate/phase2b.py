from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path

from .. import __version__
from .export import parse_ascii_stl, write_mesh_stl
from .mesh import (
    MeshBody, SinglePanelGeometry, concave_grout_mesh,
    concave_grout_spatial_validation, debossed_x_monotone_base_mesh,
    fabrication_perimeter_bounds, mesh_validation,
)
from .model import FabricationProfile, Point2MM, ResolvedFabricationModel
from .phase2a import (
    Phase2AReviewPackage, SeamPath, _panel_tile_body,
    build_phase2a_model, derive_vertical_grout_seam,
)


PRODUCTION_ARTIFACT_NAME = "Mosaica Fabricate Production Baseline Review"
PANEL_ID_CELL_MM = 1.0
PANEL_ID_DEBOSS_DEPTH_MM = 0.35
LEGACY_5_4_PROFILE = FabricationProfile(
    profile_id="mosaica-legacy-validated-5-4-mm-baseline",
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
PRODUCTION_PROFILE = replace(
    LEGACY_5_4_PROFILE,
    profile_id="mosaica-validated-production-baseline",
    version=2,
    base_thickness_mm=1.5,
    straight_tile_relief_mm=1.3,
)


@dataclass(frozen=True)
class PanelIdentity:
    panel_id: str
    row: int
    column: int
    top_orientation: str = "artwork_top"


@dataclass(frozen=True)
class ProductionPrototype:
    model: ResolvedFabricationModel
    seam: SeamPath
    panels: tuple[SinglePanelGeometry, ...]
    tile_ownership: tuple[tuple[str, str], ...]
    panel_identities: tuple[PanelIdentity, ...]
    marking_cells: tuple[tuple[str, tuple[tuple[Point2MM, ...], ...]], ...]


def panel_identifier(row: int, column: int) -> str:
    """Return spreadsheet-style row letters plus a one-based column."""

    if row < 0 or column < 0:
        raise ValueError("Panel row and column cannot be negative.")
    value = row + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{column + 1}"


_GLYPHS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "1": ("010", "110", "010", "010", "010", "010", "111"),
    "2": ("11110", "00001", "00001", "01110", "10000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("10010", "10010", "10010", "11111", "00010", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("111", "010", "010", "010", "010", "010", "111"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


def _marking_dimensions(panel_id: str) -> tuple[float, float]:
    pitch, glyph_gap = 1.45, 1.45 * 2
    glyphs = tuple(_GLYPHS[value] for value in panel_id)
    widths = [len(glyph[0]) * pitch - (pitch - PANEL_ID_CELL_MM) for glyph in glyphs]
    return sum(widths) + glyph_gap * (len(glyphs) - 1), 7 * pitch - (pitch - PANEL_ID_CELL_MM)


def _marking_cells_at(
    identity: PanelIdentity, origin_x: float, origin_y: float,
) -> tuple[tuple[Point2MM, ...], ...]:
    pixel, pitch, glyph_gap = PANEL_ID_CELL_MM, 1.45, 1.45 * 2
    glyphs = tuple(_GLYPHS[value] for value in identity.panel_id)
    widths = [len(glyph[0]) * pitch - (pitch - pixel) for glyph in glyphs]
    cells = []
    cursor_x = origin_x
    for glyph, width in zip(glyphs, widths):
        for row, values in enumerate(glyph):
            for column, value in enumerate(values):
                if value == "1":
                    x, y = cursor_x + column * pitch, origin_y + row * pitch
                    cells.append(((x, y), (x + pixel, y), (x + pixel, y + pixel), (x, y + pixel)))
        cursor_x += width + glyph_gap
    return tuple(cells)


def _marking_cells(
    identity: PanelIdentity,
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[Point2MM, ...], ...]:
    mark_width, mark_height = _marking_dimensions(identity.panel_id)
    left, _top, right, bottom = bounds
    inset = 8.0
    origin_x = left + inset if identity.column == 0 else right - inset - mark_width
    origin_y = bottom - inset - mark_height
    return _marking_cells_at(identity, origin_x, origin_y)


def build_production_model(
    profile: FabricationProfile = PRODUCTION_PROFILE,
) -> ResolvedFabricationModel:
    return replace(build_phase2a_model(), profile=profile)


def _panel_outline(
    panel_id: str, seam: SeamPath,
    bounds: tuple[float, float, float, float],
) -> tuple[Point2MM, ...]:
    left, top, right, bottom = bounds
    seam_top_to_bottom = seam.points_mm
    if panel_id == "A1":
        return ((left, top),) + seam_top_to_bottom + ((left, bottom),)
    return ((right, top), (right, bottom)) + tuple(reversed(seam_top_to_bottom))


def build_production_prototype(
    profile: FabricationProfile = PRODUCTION_PROFILE,
) -> ProductionPrototype:
    model = build_production_model(profile)
    seam, phase2a_ownership = derive_vertical_grout_seam(model)
    ownership = {
        tile_id: ("A1" if owner == "A" else "A2")
        for tile_id, owner in phase2a_ownership.items()
    }
    bounds = fabrication_perimeter_bounds(model)
    identities = (PanelIdentity("A1", 0, 0), PanelIdentity("A2", 0, 1))
    panels = []
    all_marking_cells = []
    for identity in identities:
        panel_id = identity.panel_id
        panel_tiles = tuple(tile for tile in model.tiles if ownership[tile.tile_id] == panel_id)
        if identity.column == 0:
            left_at_y, right_at_y = (lambda _y: bounds[0]), seam.x_at
        else:
            left_at_y, right_at_y = seam.x_at, (lambda _y: bounds[2])
        outline = _panel_outline(panel_id, seam, bounds)
        cells = _marking_cells(identity, bounds)
        all_marking_cells.append((panel_id, cells))
        base_triangles = debossed_x_monotone_base_mesh(
            outline, left_at_y, right_at_y, cells,
            top_z_mm=model.profile.base_thickness_mm,
            deboss_depth_mm=PANEL_ID_DEBOSS_DEPTH_MM,
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
            channel_tiles = tuple(
                tile for tile in panel_tiles
                if tile.material_channel_id == channel.channel_id
            )
            if channel_tiles:
                bodies.append(_panel_tile_body(model, panel_id, channel, channel_tiles))
        panel_bounds = (
            min(body.bounds_mm[0] for body in bodies),
            min(body.bounds_mm[1] for body in bodies),
            max(body.bounds_mm[3] for body in bodies),
            max(body.bounds_mm[4] for body in bodies),
        )
        panels.append(SinglePanelGeometry(panel_id, model, tuple(bodies), panel_bounds))
    return ProductionPrototype(
        model, seam, tuple(panels), tuple(sorted(ownership.items())),
        identities, tuple(all_marking_cells),
    )


def _signature(prototype: ProductionPrototype) -> str:
    payload = {
        "model": prototype.model.to_dict(),
        "seam": prototype.seam.points_mm,
        "identities": [identity.__dict__ for identity in prototype.panel_identities],
        "panels": [[(body.body_id, body.triangles) for body in panel.bodies] for panel in prototype.panels],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def generate_production_review_package(output_directory: str | Path) -> Phase2AReviewPackage:
    prototype = build_production_prototype()
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    ownership = dict(prototype.tile_ownership)
    identities = {value.panel_id: value for value in prototype.panel_identities}
    marking_cells = dict(prototype.marking_cells)
    stl_paths, records = [], []
    for panel in prototype.panels:
        for body in panel.bodies:
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
                "owner": panel.panel_id,
                "body_id": body.body_id,
                "logical_channel": body.material_channel_id,
                "filename": filename,
                "bounds_mm": list(body.bounds_mm),
                "tile_ids": list(body.tile_ids),
                "geometry_signature_sha256": sha256(json.dumps(body.triangles, separators=(",", ":")).encode()).hexdigest(),
                "stl_sha256": sha256(path.read_bytes()).hexdigest(),
                "mesh_validation": source_validation,
                "stl_round_trip": {"valid": len(parsed.triangles) == len(body.triangles) and round_trip_validation["watertight"], "mesh_validation": round_trip_validation},
            }
            if body.material_channel_id == "grout-thinset":
                tiles = tuple(tile for tile in prototype.model.tiles if ownership[tile.tile_id] == panel.panel_id)
                record["concave_surface_validation"] = concave_grout_spatial_validation(prototype.model, body.triangles, tiles=tiles)
            records.append(record)
    signature = _signature(prototype)
    manifest = {
        "schema": {"name": "mosaica-fabricate-production-review", "version": 1},
        "artifact_name": PRODUCTION_ARTIFACT_NAME,
        "application_version": __version__,
        "fabrication_profile": prototype.model.profile.__dict__,
        "validated_dimensions": {"straight_tile_relief_mm": 1.3, "rounded_crown_mm": 0.8, "total_tile_relief_mm": 2.1, "finished_total_z_mm": 4.6, "panel_id_cell_mm": PANEL_ID_CELL_MM, "backside_deboss_depth_mm": PANEL_ID_DEBOSS_DEPTH_MM},
        "provisionally_retained_dimensions": {"grout_gap_mm": 1.8, "grout_depression_mm": 0.30},
        "panels": [{
            **identities[panel.panel_id].__dict__,
            "bounds_mm": list(panel.fabrication_bounds_mm),
            "tile_count": sum(1 for owner in ownership.values() if owner == panel.panel_id),
            "backside_marking": {"content": panel.panel_id, "cell_size_mm": PANEL_ID_CELL_MM, "depth_mm": PANEL_ID_DEBOSS_DEPTH_MM, "mirrored": False, "reading_direction": "left_to_right_when_viewed_from_backside", "cell_count": len(marking_cells[panel.panel_id])},
            "neighbors": [{"panel_id": "A2" if panel.panel_id == "A1" else "A1", "relationship": "natural_grout_line_seam"}],
        } for panel in prototype.panels],
        "seam": {"orientation": prototype.seam.orientation, "location_mm": [list(point) for point in prototype.seam.points_mm], "tile_cuts_created": 0},
        "panel_connection": {"type": "natural_grout_line_seam", "dedicated_connector_geometry": False, "tile_cuts_created": 0, "permanent_structure": "ACP_backer_and_adhesive"},
        "process_candidate": {
            "printer": "Bambu Lab P1S",
            "preset": "0.20 mm Standard",
            "wall_loops": 2,
            "adaptive_variable_layer_height": True,
            "default_surface_finish": "Standard / no ironing",
            "ironing": "optional",
            "premium_ironing_profile": {
                "surface": "Topmost surfaces",
                "pattern": "Concentric",
                "flow_percent": 18,
                "speed_mm_s": 30,
                "line_spacing_mm": 0.15,
            },
            "binding": "production process direction for future 3MF export",
        },
        "coordinate_system": {"units": "mm", "origin": prototype.model.origin, "export": "shared global assembly coordinates"},
        "body_channel_ownership": records,
        "geometry_signature_sha256": signature,
        "automatic_panelization": False,
        "bambu_import_instructions": [
            "Select every A1 and A2 STL together in Bambu Studio.",
            "Choose load as one object with multiple parts so shared global coordinates are retained.",
            "Assign Base, Grout/Thinset, and Tile Color bodies to the intended filaments.",
            "Print backside down and artwork face up; the readable debossed panel ID faces the build plate.",
            "Use the 0.20 mm Standard P1S baseline with two wall loops and Adaptive Variable Layer Height on.",
            "Default to Standard/no ironing; optional premium ironing uses Topmost surfaces, Concentric, 18% flow, 30 mm/s, and 0.15 mm line spacing.",
            "Bond both panels to the same ACP backer and mate A1 to A2 along the natural grout-line seam.",
        ],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Phase2AReviewPackage(output, manifest_path, tuple(stl_paths), signature)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the validated Mosaica Fabricate production-baseline review package.")
    parser.add_argument("--out", default="fabricate_production_review")
    arguments = parser.parse_args(argv)
    package = generate_production_review_package(arguments.out)
    print(f"Fabricate production review: {package.output_directory}")
    print(f"Manifest: {package.manifest_path}")
    print(f"Geometry signature: {package.geometry_signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

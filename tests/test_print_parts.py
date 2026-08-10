import json
from math import isclose, sqrt
from pathlib import Path
import sys

import pytest

from mosaic_engine.cli import main
from mosaic_engine.geometry import build_panel_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.print_parts import (
    build_print_parts_manifest,
    calibration_polygon,
    export_calibration_package,
    export_print_parts_package,
    inches_to_mm,
    offset_polygon,
    triangulate_extrusion,
)
from mosaic_engine.project import MosaicProject


PALETTE = (
    PaletteColor("Black", (17, 17, 17), "BK-01"),
    PaletteColor("White", (245, 243, 237), "WH-02"),
)


def _project(tmp_path):
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        target_width_in=4.0,
        target_height_in=3.0,
        grout_width_in=0.0625,
        quantization_mode="bw",
    )
    geometry = build_panel_geometry(config, 4.0, 3.0)
    grid = [
        [(row + column) % 2 for column in range(geometry.columns)]
        for row in range(geometry.rows)
    ]
    project = MosaicProject.from_result(MosaicResult(
        columns=geometry.columns,
        rows=geometry.rows,
        grid=grid,
        palette=PALETTE,
        source_path=tmp_path / "source.svg",
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    editable = next(value for value in geometry.placements if value.piece_type == "full")
    project.set_override(
        editable.row, editable.column,
        1 - project.generated_value(editable.row, editable.column),
    )
    return project


def _edge_counts(triangles):
    counts = {}
    for triangle in triangles:
        for first, second in zip(triangle, triangle[1:] + triangle[:1]):
            edge = tuple(sorted((first, second)))
            counts[edge] = counts.get(edge, 0) + 1
    return counts


def test_inch_to_mm_and_nominal_full_hex_dimensions():
    assert inches_to_mm(1.0) == 25.4
    polygon = calibration_polygon("hex", 1.0)
    width = max(x for x, _ in polygon) - min(x for x, _ in polygon)
    height = max(y for _, y in polygon) - min(y for _, y in polygon)
    assert isclose(width, 25.4, abs_tol=1e-8)
    assert isclose(height, 2 * 25.4 / sqrt(3), abs_tol=1e-8)


def test_clipped_geometry_preserved_and_effective_palette_used(tmp_path):
    project = _project(tmp_path)
    manifest = build_print_parts_manifest(project)
    cut = next(value for value in project.geometry.placements if value.piece_type != "full")
    mapping = next(
        value for value in manifest.placements
        if value.row == cut.row + 1 and value.column == cut.column + 1
    )
    part = next(value for value in manifest.parts if value.geometry_hash == mapping.geometry_hash)
    expected_width = inches_to_mm(
        max(x for x, _ in cut.vertices_in) - min(x for x, _ in cut.vertices_in)
    )
    assert isclose(part.nominal_width_mm, expected_width, abs_tol=1e-7)
    assert mapping.palette_index == project.effective_index(cut.row, cut.column)


def test_geometry_deduplication_mapping_and_quantities(tmp_path):
    project = _project(tmp_path)
    manifest = build_print_parts_manifest(project)
    visible = [
        value for value in project.geometry.placements
        if value.piece_type != "outside"
    ]
    assert len(manifest.placements) == len(visible)
    assert sum(part.quantity for part in manifest.parts) == len(visible)
    assert len(manifest.parts) < len(visible)
    assert manifest.summary["full_tile_quantity"] == sum(
        value.piece_type == "full" for value in visible
    )
    assert len({value.tile_id for value in manifest.placements}) == len(visible)
    assert all(value.part_filename for value in manifest.placements)


def test_extrusion_is_watertight_with_correct_face_normals():
    polygon = calibration_polygon("hex", 1.0)
    triangles = triangulate_extrusion(polygon, 3.0)
    assert all(count == 2 for count in _edge_counts(triangles).values())
    bottom = [triangle for triangle in triangles if all(v[2] == 0 for v in triangle)]
    top = [triangle for triangle in triangles if all(v[2] == 3 for v in triangle)]
    def normal_z(triangle):
        a, b, c = triangle
        return ((b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0]))
    assert all(normal_z(value) < 0 for value in bottom)
    assert all(normal_z(value) > 0 for value in top)


def test_hash_and_triangulation_are_deterministic(tmp_path):
    project = _project(tmp_path)
    first = build_print_parts_manifest(project).to_dict()
    second = build_print_parts_manifest(project).to_dict()
    assert first == second
    polygon = calibration_polygon("hex", 1.0)
    assert triangulate_extrusion(polygon, 3.0) == triangulate_extrusion(polygon, 3.0)


def test_positive_and_negative_xy_offsets_and_invalid_rejection():
    polygon = calibration_polygon("hex", 1.0)
    nominal_width = max(x for x, _ in polygon) - min(x for x, _ in polygon)
    expanded = offset_polygon(polygon, 0.1)
    contracted = offset_polygon(polygon, -0.1)
    width = lambda value: max(x for x, _ in value) - min(x for x, _ in value)
    assert width(expanded) > nominal_width
    assert width(contracted) < nominal_width
    with pytest.raises(ValueError, match="collapses|invalidates"):
        offset_polygon(polygon, -20.0)


def test_package_exports_stls_and_does_not_mutate_project(tmp_path):
    project = _project(tmp_path)
    generated, overrides, effective = (
        project.generated_grid, project.overrides, project.effective_grid,
    )
    paths = export_print_parts_package(project, tmp_path / "parts")
    manifest = json.loads(paths["manifest"].read_text())
    assert len(list((tmp_path / "parts").glob("*.stl"))) == len(manifest["parts"])
    assert all(path.read_text().startswith("solid mosaic_tile") for path in paths.values() if path.suffix == ".stl")
    assert project.generated_grid == generated
    assert project.overrides == overrides
    assert project.effective_grid == effective


def test_calibration_output_has_five_offsets(tmp_path):
    paths = export_calibration_package(tmp_path / "calibration")
    manifest = json.loads(paths["manifest"].read_text())
    assert [value["xy_offset_mm"] for value in manifest["pieces"]] == [
        -0.1, -0.05, 0.0, 0.05, 0.1,
    ]
    assert len(list((tmp_path / "calibration").glob("*.stl"))) == 5


def test_cli_print_parts_and_calibration(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    project_path = project.save(tmp_path / "project.json")
    before = project_path.read_bytes()
    parts = tmp_path / "parts"
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine", "--print-parts", str(project_path), "--out", str(parts),
        "--thickness-mm", "3.2", "--xy-offset-mm", "0.05",
    ])
    main()
    assert "Printable parts output:" in capsys.readouterr().out
    assert project_path.read_bytes() == before
    assert json.loads((parts / "print_parts_manifest.json").read_text())["thickness_mm"] == 3.2

    calibration = tmp_path / "calibration"
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine", "--print-calibration", "--shape", "hex", "--tile", "1",
        "--thickness-mm", "3.0", "--out", str(calibration),
    ])
    main()
    assert "Print calibration output:" in capsys.readouterr().out
    assert (calibration / "calibration_manifest.json").exists()

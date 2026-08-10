import csv
import json
from math import ceil, isclose
from pathlib import Path
import sys

from mosaic_engine.cli import main
from mosaic_engine.fabrication import (
    build_fabrication_data,
    export_fabrication_package,
)
from mosaic_engine.geometry import build_panel_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject


PALETTE = (
    PaletteColor("Black", (17, 17, 17), "BK-01"),
    PaletteColor("Warm White", (245, 243, 237), "WW-02"),
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
        [
            (row + column) % 2
            for column in range(geometry.columns)
        ]
        for row in range(geometry.rows)
    ]
    project = MosaicProject.from_result(MosaicResult(
        columns=geometry.columns,
        rows=geometry.rows,
        grid=grid,
        palette=PALETTE,
        source_path=tmp_path / "approved-artwork.svg",
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    editable = next(
        value for value in geometry.placements
        if value.piece_type == "full" and grid[value.row][value.column] == 0
    )
    project.set_override(editable.row, editable.column, 1)
    return project, editable


def test_material_schedule_uses_effective_assignments_and_skus(tmp_path):
    project, overridden = _project(tmp_path)
    generated = project.generated_grid
    data = build_fabrication_data(project)
    black, white = data.materials

    assert project.effective_index(overridden.row, overridden.column) == 1
    assert generated[overridden.row][overridden.column] == 0
    assert black.sku == "BK-01"
    assert white.sku == "WW-02"
    assert black.visible_piece_count == (
        black.full_tile_count + black.cut_piece_count
    )
    expected_white = sum(
        project.effective_index(value.row, value.column) == 1
        for value in project.geometry.placements
        if value.piece_type != "outside"
    )
    assert white.visible_piece_count == expected_white


def test_full_cut_outside_and_piece_fraction_totals(tmp_path):
    project, _ = _project(tmp_path)
    data = build_fabrication_data(project)
    placements = project.geometry.placements
    full = sum(value.piece_type == "full" for value in placements)
    cut = sum(
        value.piece_type not in {"full", "outside"}
        for value in placements
    )
    outside = sum(value.piece_type == "outside" for value in placements)

    assert data.summary["total_full_pieces"] == full
    assert data.summary["total_cut_pieces"] == cut
    assert data.summary["outside_placements"] == outside
    assert len(data.cut_pieces) == cut
    assert isclose(
        data.summary["total_equivalent_tile_area"],
        sum(
            value.piece_fraction for value in placements
            if value.piece_type != "outside"
        ),
    )
    assert isclose(
        sum(value.equivalent_full_tile_area for value in data.materials),
        data.summary["total_equivalent_tile_area"],
    )


def test_purchase_quantity_uses_documented_waste_formula(tmp_path):
    project, _ = _project(tmp_path)
    data = build_fabrication_data(project, waste_factor=0.15)

    for material in data.materials:
        assert material.area_based_minimum_quantity == ceil(
            material.equivalent_full_tile_area
        )
        assert material.recommended_purchase_quantity == ceil(
            material.equivalent_full_tile_area * 1.15
            + material.cut_piece_count * 0.15
        )
    assert "No nesting" in data.summary["purchase_limitation"]


def test_cut_schedule_preserves_exact_polygon_and_panel_edges(tmp_path):
    project, _ = _project(tmp_path)
    data = build_fabrication_data(project)
    record = data.cut_pieces[0]
    placement = project.geometry.placement(record.row - 1, record.column - 1)

    assert record.tile_id == (
        f"placement-{(record.row - 1) * project.columns + record.column - 1:06d}"
    )
    assert record.vertices_in == placement.vertices_in
    assert record.piece_fraction == placement.piece_fraction
    assert record.bounding_width_in == (
        max(x for x, _ in placement.vertices_in)
        - min(x for x, _ in placement.vertices_in)
    )
    assert record.clipped_edges


def test_row_guide_is_left_to_right_with_stable_ids(tmp_path):
    project, _ = _project(tmp_path)
    data = build_fabrication_data(project)

    for row_number, row in enumerate(data.rows, 1):
        assert [value.column for value in row] == sorted(
            value.column for value in row
        )
        assert [value.sequence for value in row] == list(
            range(1, len(row) + 1)
        )
        assert all(value.row == row_number for value in row)
        assert all(value.tile_id.startswith("placement-") for value in row)


def test_fabrication_package_writes_svg_and_schedules_without_mutation(
    tmp_path,
):
    project, overridden = _project(tmp_path)
    generated = project.generated_grid
    overrides = project.overrides
    effective = project.effective_grid
    paths = export_fabrication_package(
        project, tmp_path / "fabrication", waste_factor=0.10
    )

    assert all(path.exists() for path in paths.values())
    assert set(paths) == {
        "material_schedule",
        "cut_piece_schedule",
        "row_build_guide_csv",
        "row_build_guide_text",
        "assembly_map",
        "project_summary",
        "manifest",
    }
    svg = paths["assembly_map"].read_text()
    tile_id = (
        f"placement-{overridden.row * project.columns + overridden.column:06d}"
    )
    assert f'id="{tile_id}"' in svg
    assert 'fill="#F5F3ED"' in svg
    assert 'class="tile cut-piece"' in svg
    assert "Finished panel:" in svg
    assert "P01 Black" in svg
    assert 'fill="#f3f4f6"' in svg
    assert project.generated_grid == generated
    assert project.overrides == overrides
    assert project.effective_grid == effective


def test_cli_fabrication_export(tmp_path, monkeypatch, capsys):
    project, _ = _project(tmp_path)
    project_path = project.save(tmp_path / "project.json")
    before = project_path.read_bytes()
    output = tmp_path / "build-package"
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine",
        "--fabrication", str(project_path),
        "--out", str(output),
        "--waste", "0.20",
    ])

    main()

    assert "Fabrication output:" in capsys.readouterr().out
    assert project_path.read_bytes() == before
    manifest = json.loads((output / "fabrication_manifest.json").read_text())
    assert manifest["waste_factor"] == 0.20
    with (output / "material_schedule.csv").open() as source:
        rows = list(csv.DictReader(source))
    assert {row["sku"] for row in rows} == {"BK-01", "WW-02"}

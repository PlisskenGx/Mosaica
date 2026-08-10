from pathlib import Path

import pytest

from mosaic_engine.benchmark import (
    analyze_benchmark_projects,
    analyze_project,
    benchmark_reports_json,
    connected_correction_regions,
    format_benchmark_reports,
)
from mosaic_engine.evidence import BWEvidence, TileEvidence
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject


PALETTE = (
    PaletteColor("Black", (0, 0, 0)),
    PaletteColor("White", (255, 255, 255)),
)


def _project() -> MosaicProject:
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=5,
        rows=3,
        quantization_mode="bw",
        coverage_threshold=0.45,
    )
    geometry = build_geometry(config, 5, 3)
    grid = [[1] * 5 for _ in range(3)]
    grid[2][4] = 0
    result = MosaicResult(
        columns=5,
        rows=3,
        grid=grid,
        palette=PALETTE,
        source_path=Path("missing.png"),
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    )
    project = MosaicProject.from_result(result)
    project.set_override(0, 0, 0)
    project.set_override(0, 1, 0)
    project.set_override(2, 4, 1)
    project.set_override(1, 4, 1)  # Stored no-op.
    return project


def _tile(row, column, coverage):
    return TileEvidence(
        row=row,
        column=column,
        raw_coverage=coverage,
        first_ring=(),
        second_ring=(),
        first_ring_foreground=0,
        second_ring_foreground=0,
        source_foreground_at_center=False,
        source_edge_distance_in=None,
        source_boundary_orientation_deg=None,
        source_centerline_distance_in=None,
        local_stroke_width_in=None,
    )


def test_benchmark_reports_real_changes_directions_and_counts():
    project = _project()
    report = analyze_project(project)

    assert report.full_tiles == 15
    assert report.stored_overrides == 4
    assert report.real_changed_overrides == 3
    assert report.white_to_black == 2
    assert report.black_to_white == 1
    assert report.generated_black == 1
    assert report.effective_black == 2
    assert report.generated_boundary_edges >= 0
    assert report.effective_boundary_edges >= 0
    assert sum(report.generated_same_neighbor_histogram.values()) == 3


def test_connected_regions_use_physical_hex_neighbors():
    regions = connected_correction_regions(_project())

    assert [region.size for region in regions] == [2, 1]
    assert regions[0].coordinates == ((0, 0), (0, 1))
    assert regions[0].white_to_black == 2
    assert regions[1].black_to_white == 1


def test_benchmark_reports_source_coverage_disagreement():
    evidence = BWEvidence(
        tiles={
            (0, 0): _tile(0, 0, 0.1),
            (0, 1): _tile(0, 1, 0.2),
            (2, 4): _tile(2, 4, 0.9),
        },
        coverage_threshold=0.45,
        source_resolution=(100, 100),
    )

    report = analyze_project(_project(), evidence=evidence)

    assert report.coverage_disagreements == 3
    assert report.white_to_black_coverage_disagreements == 2
    assert report.black_to_white_coverage_disagreements == 1
    assert report.changed_mean_coverage == pytest.approx(0.4)


def test_multiple_saved_benchmark_projects_are_supported(tmp_path):
    first = _project().save(tmp_path / "first.json")
    second = _project().save(tmp_path / "second.json")

    reports = analyze_benchmark_projects([first, second])

    assert len(reports) == 2
    assert [Path(report.project_path).name for report in reports] == [
        "first.json",
        "second.json",
    ]
    assert all(report.real_changed_overrides == 3 for report in reports)


def test_benchmark_json_output_round_trips():
    reports = (analyze_project(_project(), project_path="reference.json"),)

    encoded = benchmark_reports_json(reports)
    decoded = __import__("json").loads(encoded)

    assert decoded == [reports[0].to_dict()]
    assert decoded[0]["real_changed_overrides"] == 3
    assert decoded[0]["correction_regions"][0]["coordinates"] == [
        [0, 0],
        [0, 1],
    ]


def test_human_readable_benchmark_output():
    output = format_benchmark_reports([analyze_project(_project())])

    assert "Real changed overrides: 3" in output
    assert "2 white -> black" in output
    assert "Correction region sizes: 2, 1" in output

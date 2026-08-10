from math import isclose

from PIL import Image, ImageDraw

from mosaic_engine.evidence import (
    compute_bw_evidence,
    compute_project_bw_evidence,
    physical_neighbor_rings,
)
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject


def test_pointy_hex_second_ring_uses_physical_adjacency():
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=5,
        rows=5,
    )
    geometry = build_geometry(config, 5, 5)

    first, second = physical_neighbor_rings(2, 2, geometry, config)

    assert len(first) == 6
    assert len(second) == 12
    assert (2, 2) not in second
    assert set(first).isdisjoint(second)


def test_bw_evidence_exposes_coverage_and_neighbor_features():
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=3,
        rows=3,
        quantization_mode="bw",
        coverage_threshold=0.45,
    )
    geometry = build_geometry(config, 3, 3)
    image = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 149, 299), fill="black")

    evidence = compute_bw_evidence(
        image, geometry, config, source_analysis_width=128
    )
    tile = evidence.tile(1, 1)

    assert 0.0 <= tile.raw_coverage <= 1.0
    assert len(tile.first_ring) == 6
    assert tile.second_ring
    assert 0 <= tile.first_ring_foreground <= len(tile.first_ring)
    assert 0 <= tile.second_ring_foreground <= len(tile.second_ring)


def test_source_evidence_reports_edge_orientation_and_stroke_scale():
    config = MosaicConfig(
        columns=1,
        rows=1,
        quantization_mode="bw",
    )
    geometry = build_geometry(config, 1, 1)
    image = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(image).rectangle((50, 0, 149, 199), fill="black")

    tile = compute_bw_evidence(
        image, geometry, config, source_analysis_width=200
    ).tile(0, 0)

    assert isclose(tile.raw_coverage, 0.5, abs_tol=0.05)
    assert tile.source_foreground_at_center
    assert tile.source_edge_distance_in is not None
    assert tile.source_edge_distance_in > 0.2
    assert tile.source_boundary_orientation_deg is not None
    assert isclose(tile.source_boundary_orientation_deg, 90.0, abs_tol=5.0)
    assert tile.source_centerline_distance_in is not None
    assert tile.local_stroke_width_in is not None
    assert 0.4 < tile.local_stroke_width_in < 0.6


def test_evidence_does_not_change_generation_inputs():
    config = MosaicConfig(
        columns=2,
        rows=1,
        quantization_mode="bw",
    )
    geometry = build_geometry(config, 2, 1)
    image = Image.new("RGB", (20, 10), "black")
    before = image.tobytes()

    compute_bw_evidence(image, geometry, config, source_analysis_width=20)

    assert image.tobytes() == before


def test_project_evidence_is_invariant_to_manual_overrides(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGB", (100, 50), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 49, 49), fill="black")
    image.save(source)
    config = MosaicConfig(
        columns=2,
        rows=1,
        quantization_mode="bw",
        fit="stretch",
    )
    geometry = build_geometry(config, 2, 1)
    project = MosaicProject.from_result(MosaicResult(
        columns=2,
        rows=1,
        grid=[[0, 1]],
        palette=(
            PaletteColor("Black", (0, 0, 0)),
            PaletteColor("White", (255, 255, 255)),
        ),
        source_path=source,
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))

    without_overrides = compute_project_bw_evidence(
        project, source_analysis_width=100
    )
    project.set_override(0, 0, 1)
    project.set_override(0, 1, 0)
    with_overrides = compute_project_bw_evidence(
        project, source_analysis_width=100
    )

    assert project.effective_grid != [list(project.generated_grid[0])]
    assert with_overrides == without_overrides
    assert with_overrides.to_dict() == without_overrides.to_dict()

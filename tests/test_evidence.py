from math import isclose
import builtins
import json

import pytest
from PIL import Image, ImageDraw

from mosaica.evidence import (
    BWEvidenceCache,
    build_evidence_cache,
    cache_project_bw_evidence,
    compute_bw_evidence,
    compute_project_bw_evidence,
    evidence_cache_validity,
    physical_neighbor_rings,
    resolve_project_bw_evidence,
)
from mosaica.geometry import build_geometry
from mosaica.model import MosaicConfig, MosaicResult, PaletteColor
from mosaica.project import MosaicProject
from mosaica.refinement import generate_refinement_proposals


PALETTE = (
    PaletteColor("Black", (0, 0, 0)),
    PaletteColor("White", (255, 255, 255)),
)


def _bw_project(tmp_path, *, source_name="source.png"):
    source = tmp_path / source_name
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=4,
        rows=4,
        quantization_mode="bw",
        fit="stretch",
    )
    geometry = build_geometry(config, 4, 4)
    project = MosaicProject.from_result(MosaicResult(
        columns=4,
        rows=4,
        grid=[
            [1, 0, 1, 1],
            [1, 0, 1, 1],
            [1, 1, 0, 1],
            [1, 1, 0, 1],
        ],
        palette=PALETTE,
        source_path=source,
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    return project, source


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


def test_cached_evidence_survives_project_json_round_trip(tmp_path):
    project, source = _bw_project(tmp_path)
    Image.new("RGB", (120, 120), "black").save(source)
    evidence = cache_project_bw_evidence(
        project, source_analysis_width=120
    )
    path = project.save(tmp_path / "cached.json")

    loaded = MosaicProject.load(path)

    assert loaded.bw_evidence_cache is not None
    assert loaded.bw_evidence_cache.evidence == evidence
    assert BWEvidenceCache.from_dict(
        json.loads(path.read_text())["bw_evidence_cache"]
    ) == loaded.bw_evidence_cache
    assert evidence_cache_validity(loaded, loaded.bw_evidence_cache) == (
        True,
        None,
    )


def test_cached_evidence_is_invariant_to_overrides(tmp_path):
    project, source = _bw_project(tmp_path)
    Image.new("RGB", (80, 80), "white").save(source)
    cache_project_bw_evidence(project, source_analysis_width=80)
    before = project.bw_evidence_cache.to_dict()

    project.set_override(0, 0, 0)
    project.set_override(0, 1, 1)

    assert project.bw_evidence_cache.to_dict() == before
    assert evidence_cache_validity(project, project.bw_evidence_cache) == (
        True,
        None,
    )


def test_cached_and_source_evidence_generate_identical_proposals(tmp_path):
    project, source = _bw_project(tmp_path)
    image = Image.new("RGB", (160, 160), "white")
    ImageDraw.Draw(image).polygon(
        [(40, 0), (75, 0), (125, 160), (90, 160)], fill="black"
    )
    image.save(source)
    source_evidence = compute_project_bw_evidence(project)
    expected = generate_refinement_proposals(project, source_evidence)
    project.set_bw_evidence_cache(build_evidence_cache(
        project, source_evidence
    ))
    path = project.save(tmp_path / "cached.json")
    source.unlink()

    loaded = MosaicProject.load(path)
    cached_evidence = resolve_project_bw_evidence(loaded)

    assert cached_evidence == source_evidence
    assert generate_refinement_proposals(loaded, cached_evidence) == expected


def test_cached_svg_evidence_does_not_import_cairosvg(
    tmp_path, monkeypatch
):
    project, source = _bw_project(tmp_path, source_name="source.svg")
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="5" height="10" fill="black"/></svg>'
    )
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 49, 99), fill="black")
    evidence = compute_bw_evidence(image, project.geometry, project.config)
    project.set_bw_evidence_cache(build_evidence_cache(project, evidence))
    path = project.save(tmp_path / "cached-svg.json")
    loaded = MosaicProject.load(path)
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "cairosvg":
            raise OSError("Cairo unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    assert resolve_project_bw_evidence(loaded) == evidence
    assert generate_refinement_proposals(loaded, evidence)


def test_stale_cached_evidence_is_detected_with_actionable_error(tmp_path):
    project, source = _bw_project(tmp_path)
    Image.new("RGB", (100, 100), "black").save(source)
    cache_project_bw_evidence(project, source_analysis_width=100)
    path = project.save(tmp_path / "cached.json")
    data = json.loads(path.read_text())
    data["config"]["artwork_scale"] = 1.1
    path.write_text(json.dumps(data))
    source.unlink()
    loaded = MosaicProject.load(path)

    valid, reason = evidence_cache_validity(
        loaded, loaded.bw_evidence_cache
    )

    assert valid is False
    assert "configuration changed" in reason
    with pytest.raises(RuntimeError, match="stale.*--cache-evidence"):
        resolve_project_bw_evidence(loaded)

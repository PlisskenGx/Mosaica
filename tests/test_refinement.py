from dataclasses import replace
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw

from mosaic_engine.cli import main
from mosaic_engine.evidence import BWEvidence, TileEvidence
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject
from mosaic_engine.refinement import generate_refinement_proposals


PALETTE = (
    PaletteColor("Black", (0, 0, 0)),
    PaletteColor("White", (255, 255, 255)),
)


def _project_and_evidence(*, clipped=False):
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=5,
        rows=5,
        quantization_mode="bw",
        coverage_threshold=0.45,
    )
    geometry = build_geometry(config, 5, 5)
    if clipped:
        placements = list(geometry.placements)
        placements[0] = replace(
            placements[0], piece_type="edge_cut", piece_fraction=0.5
        )
        geometry = replace(geometry, placements=tuple(placements))
    grid = [[1] * 5 for _ in range(5)]
    for row in range(5):
        grid[row][2] = 0
    project = MosaicProject.from_result(MosaicResult(
        columns=5,
        rows=5,
        grid=grid,
        palette=PALETTE,
        source_path=Path("source.png"),
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    tiles = {}
    for placement in geometry.placements:
        if placement.piece_type != "full":
            continue
        row, column = placement.row, placement.column
        distance = abs(column - 2) * 0.2
        coverage = 0.30 if column == 2 else (0.36 if column in {1, 3} else 0.0)
        tiles[(row, column)] = TileEvidence(
            row=row,
            column=column,
            raw_coverage=coverage,
            first_ring=(),
            second_ring=(),
            first_ring_foreground=2,
            second_ring_foreground=3,
            source_foreground_at_center=column == 2,
            source_edge_distance_in=0.2,
            source_boundary_orientation_deg=60.0,
            source_centerline_distance_in=distance,
            local_stroke_width_in=0.8,
        )
    return project, BWEvidence(
        tiles=tiles,
        coverage_threshold=0.45,
        source_resolution=(200, 200),
    )


def test_diagonal_stroke_detection_and_regional_alternatives():
    project, evidence = _project_and_evidence()

    report = generate_refinement_proposals(project, evidence)
    alternatives = {
        proposal.alternative for proposal in report.proposals
    }

    assert report.candidates
    assert any(
        "stroke" in reason or "boundary" in reason
        for candidate in report.candidates
        for reason in candidate.reasons
    )
    assert {"expand", "contract", "shift"} <= alternatives


def test_proposals_are_deterministic_and_scores_are_decomposed():
    project, evidence = _project_and_evidence()

    first = generate_refinement_proposals(project, evidence)
    second = generate_refinement_proposals(project, evidence)

    assert first == second
    proposal = first.proposals[0]
    breakdown = proposal.alternative_breakdown.to_dict()
    assert set(breakdown) == {
        "source_agreement",
        "directional_continuity",
        "stroke_width_consistency",
        "boundary_regularity",
        "negative_space_preservation",
        "minimal_change",
        "topology_preservation",
        "total",
    }
    assert breakdown["total"] == proposal.alternative_score


def test_refinement_never_mutates_project_or_generated_grid():
    project, evidence = _project_and_evidence()
    generated_before = project.generated_grid
    overrides_before = project.overrides

    generate_refinement_proposals(project, evidence)

    assert project.generated_grid == generated_before
    assert project.overrides == overrides_before
    assert project.effective_grid == [list(row) for row in generated_before]


def test_refinement_is_independent_of_manual_overrides():
    project, evidence = _project_and_evidence()
    baseline = generate_refinement_proposals(project, evidence)

    project.set_override(2, 2, 1)
    project.set_override(2, 1, 0)
    with_overrides = generate_refinement_proposals(project, evidence)

    assert with_overrides == baseline


def test_protected_perimeter_is_excluded_from_candidates_and_changes():
    project, evidence = _project_and_evidence(clipped=True)

    report = generate_refinement_proposals(project, evidence)

    assert all(
        (0, 0) not in candidate.coordinates
        for candidate in report.candidates
    )
    assert all(
        change.tile_id != "placement-000000"
        for proposal in report.proposals
        for change in proposal.changes
    )


def _saved_cli_project(tmp_path):
    source = tmp_path / "stroke.png"
    image = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(image).polygon(
        [(70, 0), (100, 0), (140, 200), (110, 200)], fill="black"
    )
    image.save(source)
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=5,
        rows=5,
        quantization_mode="bw",
    )
    geometry = build_geometry(config, 5, 5)
    grid = [[1] * 5 for _ in range(5)]
    for row in range(5):
        grid[row][2] = 0
    return MosaicProject.from_result(MosaicResult(
        columns=5,
        rows=5,
        grid=grid,
        palette=PALETTE,
        source_path=source,
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    )).save(tmp_path / "project.json")


def test_refinement_cli_human_readable_output(tmp_path, monkeypatch, capsys):
    path = _saved_cli_project(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine", "--refine-proposals", str(path),
    ])

    main()

    output = capsys.readouterr().out
    assert "Candidate regions:" in output
    assert "Regional alternatives:" in output
    assert "Benchmark overlap:" in output
    assert path.read_bytes() == before


def test_refinement_cli_json_output(tmp_path, monkeypatch, capsys):
    path = _saved_cli_project(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine", "--refine-proposals", str(path), "--refine-json",
    ])

    main()

    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"refinement", "evaluation"}
    assert "candidates" in output["refinement"]
    assert "proposals" in output["refinement"]

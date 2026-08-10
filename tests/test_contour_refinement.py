from pathlib import Path
from dataclasses import replace

import pytest

from mosaic_engine.contour_refinement import (
    generate_contour_refinement_proposals,
)
from mosaic_engine.evidence import BWEvidence, TileEvidence
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.processing import tile_neighbors
from mosaic_engine.project import MosaicProject


PALETTE = (
    PaletteColor("Black", (0, 0, 0)),
    PaletteColor("White", (255, 255, 255)),
)


def _experiment(source_tiles, generated_tiles=None, size=9):
    generated_tiles = set(generated_tiles or source_tiles)
    source_tiles = set(source_tiles)
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
        columns=size,
        rows=size,
        quantization_mode="bw",
    )
    geometry = build_geometry(config, size, size)
    grid = [
        [0 if (row, column) in generated_tiles else 1 for column in range(size)]
        for row in range(size)
    ]
    project = MosaicProject.from_result(MosaicResult(
        columns=size,
        rows=size,
        grid=grid,
        palette=PALETTE,
        source_path=Path("synthetic.png"),
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    tiles = {}
    for placement in geometry.placements:
        coordinate = (placement.row, placement.column)
        source = coordinate in source_tiles
        tiles[coordinate] = TileEvidence(
            row=coordinate[0],
            column=coordinate[1],
            raw_coverage=0.72 if source else 0.0,
            first_ring=(),
            second_ring=(),
            first_ring_foreground=0,
            second_ring_foreground=0,
            source_foreground_at_center=source,
            source_edge_distance_in=0.15 if source else 1.5,
            source_boundary_orientation_deg=45.0 if source else None,
            source_centerline_distance_in=0.05 if source else None,
            local_stroke_width_in=1.2 if source else None,
        )
    evidence = BWEvidence(
        tiles=tiles,
        coverage_threshold=0.45,
        source_resolution=(256, 256),
    )
    return project, evidence


@pytest.mark.parametrize("stroke", [
    {(row, 4) for row in range(1, 8)},
    {(row, 1 + row // 2) for row in range(1, 8)},
    {(row, 7 - (row + 1) // 2) for row in range(1, 8)},
])
def test_contour_detection_at_several_diagonal_angles(stroke):
    project, evidence = _experiment(stroke)

    report = generate_contour_refinement_proposals(project, evidence)

    assert report.candidates
    assert report == generate_contour_refinement_proposals(project, evidence)
    for candidate in report.candidates:
        for alternative in candidate.alternatives:
            for first, second in zip(alternative.path, alternative.path[1:]):
                assert second in tile_neighbors(
                    first[0], first[1], project.rows, project.columns,
                    project.config,
                )


def test_curved_contour_produces_continuous_path_and_score_components():
    curve = {
        (1, 2), (2, 2), (3, 3), (4, 4),
        (5, 5), (6, 6), (7, 6),
    }
    project, evidence = _experiment(curve)

    report = generate_contour_refinement_proposals(project, evidence)
    alternative = report.candidates[0].alternatives[0]

    assert len(alternative.path) >= 3
    assert set(alternative.score.to_dict()) == {
        "source_trajectory_agreement",
        "directional_smoothness",
        "apparent_stroke_width",
        "boundary_complexity",
        "topology_negative_space",
        "change_cost",
        "total",
    }


def test_constant_width_stroke_exposes_source_current_and_proposed_contours():
    band = {
        (row, column)
        for row in range(1, 8)
        for column in (4, 5)
    }
    project, evidence = _experiment(band)

    candidate = generate_contour_refinement_proposals(
        project, evidence
    ).candidates[0]

    assert candidate.source_contour
    assert candidate.current_mosaic_contour
    assert candidate.alternatives[0].proposed_contour
    current_coordinates = [
        min(
            candidate.region,
            key=lambda coordinate: (
                project.geometry.placement(*coordinate).center_x_in - point[0]
            ) ** 2 + (
                project.geometry.placement(*coordinate).center_y_in - point[1]
            ) ** 2,
        )
        for point in candidate.current_mosaic_contour
    ]
    assert all(
        second in tile_neighbors(
            first[0], first[1], project.rows, project.columns, project.config
        )
        for first, second in zip(current_coordinates, current_coordinates[1:])
    )
    assert candidate.recommended_alternative is None or any(
        value.name == candidate.recommended_alternative
        and value.score.total > candidate.baseline_score.total
        for value in candidate.alternatives
    )


def test_junction_and_terminal_topology_are_not_removed_from_interior():
    junction = {(row, 4) for row in range(1, 8)} | {
        (4, column) for column in range(2, 7)
    }
    project, evidence = _experiment(junction)

    report = generate_contour_refinement_proposals(project, evidence)

    assert report.candidates
    assert all(
        not (
            change.row == 4 and change.column == 4
            and change.proposed_index == 1
        )
        for candidate in report.candidates
        for alternative in candidate.alternatives
        for change in alternative.changes
    )


def test_negative_space_is_not_crossed_by_proposed_contour():
    ring = {
        (row, column)
        for row in range(1, 8)
        for column in range(1, 8)
        if row in {1, 7} or column in {1, 7}
    }
    project, evidence = _experiment(ring)

    report = generate_contour_refinement_proposals(project, evidence)

    assert all(
        (4, 4) not in alternative.path
        for candidate in report.candidates
        for alternative in candidate.alternatives
    )
    assert all(
        not (
            change.row == 4 and change.column == 4
            and change.proposed_index == 0
        )
        for candidate in report.candidates
        for alternative in candidate.alternatives
        for change in alternative.changes
    )


def test_contour_generation_is_override_independent_and_read_only():
    stroke = {(row, row) for row in range(1, 8)}
    project, evidence = _experiment(stroke)
    generated = project.generated_grid
    baseline = generate_contour_refinement_proposals(project, evidence)

    project.set_override(3, 3, 1)
    with_override = generate_contour_refinement_proposals(project, evidence)

    assert with_override == baseline
    assert project.generated_grid == generated
    assert project.override_value(3, 3) == 1


def test_contour_changes_exclude_clipped_perimeter_pieces():
    stroke = {(row, 4) for row in range(1, 8)}
    project, evidence = _experiment(stroke)
    placements = list(project.geometry.placements)
    protected = placements[13]
    placements[13] = replace(
        protected, piece_type="edge_cut", piece_fraction=0.5
    )
    project._generated_result.geometry = replace(
        project.geometry, placements=tuple(placements)
    )
    evidence.tiles.pop((protected.row, protected.column))

    report = generate_contour_refinement_proposals(project, evidence)

    assert all(
        project.geometry.placement(change.row, change.column).piece_type
        == "full"
        for candidate in report.candidates
        for alternative in candidate.alternatives
        for change in alternative.changes
    )

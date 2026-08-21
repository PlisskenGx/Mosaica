import json

import pytest

from mosaica.designer import DesignerProjectShell
from mosaica.fabricate.mesh import mesh_validation
from mosaica.fabricate.panelize import (
    P1S_V1_SAFE_ENVELOPE_MM,
    PanelizationError,
    _candidate_cut_sets,
    _lattice_topology,
    _seam_complexity,
    build_panelized_fabrication,
    generate_panelization_package,
    panelize_model,
    theoretical_grid_counts,
)
from mosaica.fabricate.phase2a import build_phase2a_model
from mosaica.fabricate.phase2b import (
    PANEL_ID_CELL_MM, PANEL_ID_DEBOSS_DEPTH_MM, PRODUCTION_PROFILE,
)
from mosaica.fabricate.resolve import resolve_designer_project


def _model(
    across: int, down: int, orientation: str = "point_top", tile: str = "m",
):
    return resolve_designer_project(
        DesignerProjectShell.create_custom(tile, orientation, across, down),
        PRODUCTION_PROFILE,
    )


def test_theoretical_grid_uses_the_fixed_210_mm_safe_envelope():
    assert P1S_V1_SAFE_ENVELOPE_MM == (210.0, 210.0)
    assert theoretical_grid_counts(129.0, 102.0) == (1, 1)
    assert theoretical_grid_counts(209.999, 209.999) == (1, 1)
    assert theoretical_grid_counts(210.001, 200.0) == (1, 2)
    assert theoretical_grid_counts(200.0, 210.001) == (2, 1)
    assert theoretical_grid_counts(610.0, 610.0) == (3, 3)
    assert theoretical_grid_counts(610.0, 915.0) == (5, 3)


def test_small_fixture_is_one_contiguous_a1_panel():
    plan = panelize_model(build_phase2a_model())
    assert (plan.rows, plan.columns) == (1, 1)
    assert [panel.panel_id for panel in plan.panels] == ["A1"]
    assert plan.panels[0].neighbors == ()


def test_just_above_width_and_height_thresholds_split_on_natural_seams():
    width_plan = panelize_model(_model(8, 7, "point_top"))
    height_plan = panelize_model(_model(7, 8, "flat_top"))
    assert (width_plan.theoretical_rows, width_plan.theoretical_columns) == (1, 2)
    assert (width_plan.rows, width_plan.columns) == (1, 2)
    assert (height_plan.theoretical_rows, height_plan.theoretical_columns) == (2, 1)
    assert (height_plan.rows, height_plan.columns) == (2, 1)


def test_actual_irregular_bounds_are_enforced_and_can_escalate_panel_count():
    # The 404 mm field theoretically needs two rows, but every two-row natural
    # split leaves one irregular cell-union bound over 210 mm. Three rows fit.
    plan = panelize_model(_model(2, 21, "point_top", "s"))
    assert (plan.theoretical_rows, plan.theoretical_columns) == (2, 1)
    assert (plan.rows, plan.columns) == (3, 1)
    assert len(plan.panels) == 3
    assert (2, 1) in plan.attempted_layouts and (3, 1) in plan.attempted_layouts
    assert max(panel.height_mm for panel in plan.panels) <= 210.0


def test_equal_count_selection_prioritizes_area_balance_over_nearest_cut():
    model = _model(5, 11, "point_top")
    plan = panelize_model(model)
    bounds = (0.9, 0.0, 153.9, model.artwork_height_mm)
    target = (bounds[1] + bounds[3]) / 2.0
    center_y = tuple(sorted({round(tile.center_mm[1], 9) for tile in model.tiles}))
    nearest = _candidate_cut_sets(center_y, 2, (target,), limit=1)[0]
    assert plan.y_cuts_mm != nearest
    areas = [panel.area_mm2 for panel in plan.panels]
    selected_deviation = max(areas) - min(areas)
    cells, adjacency, edges = _lattice_topology(model)
    from mosaica.fabricate.panelize import _evaluate_candidate
    nearest_result = _evaluate_candidate(
        model, 2, 1, (), nearest, cells, adjacency, edges,
        P1S_V1_SAFE_ENVELOPE_MM, (), (target,),
    )
    nearest_areas = [panel.area_mm2 for panel in nearest_result[0]]
    assert selected_deviation < max(nearest_areas) - min(nearest_areas)


def test_seam_complexity_counts_turns_for_deterministic_tie_breaking():
    straight_edges = {
        ((0.0, 0.0), (0.0, 1.0)): ("a", "b"),
        ((0.0, 1.0), (0.0, 2.0)): ("c", "d"),
    }
    zigzag_edges = {
        ((0.0, 0.0), (0.0, 1.0)): ("a", "b"),
        ((0.0, 1.0), (1.0, 2.0)): ("c", "d"),
    }
    ownership = {"a": "A1", "c": "A1", "b": "A2", "d": "A2"}
    assert _seam_complexity(ownership, straight_edges)[0] == 0
    assert _seam_complexity(ownership, zigzag_edges)[0] == 1


def test_every_tile_is_assigned_once_and_panels_are_contiguous_without_holes():
    model = _model(8, 7)
    plan = panelize_model(model)
    assigned = [tile_id for panel in plan.panels for tile_id in panel.tile_ids]
    assert len(assigned) == len(set(assigned)) == len(model.tiles)
    assert set(assigned) == {tile.tile_id for tile in model.tiles}
    assert dict(plan.tile_ownership).keys() == {tile.tile_id for tile in model.tiles}


def test_panel_ids_neighbors_bounds_and_rotation_metadata_are_grid_stable():
    plan = panelize_model(_model(8, 7))
    assert [panel.panel_id for panel in plan.panels] == ["A1", "A2"]
    assert dict(plan.panels[0].neighbors) == {"right": "A2"}
    assert dict(plan.panels[1].neighbors) == {"left": "A1"}
    assert all(panel.print_rotation_degrees == 0 for panel in plan.panels)
    assert all(panel.width_mm <= 210 and panel.height_mm <= 210 for panel in plan.panels)


def test_panelized_bodies_preserve_tile_geometry_labels_and_concave_grout():
    model = _model(8, 7)
    plan = panelize_model(model)
    fabrication = build_panelized_fabrication(plan)
    assert PANEL_ID_CELL_MM == 1.0
    assert PANEL_ID_DEBOSS_DEPTH_MM == 0.35
    assert {panel_id for panel_id, _cells in fabrication.marking_cells} == {"A1", "A2"}
    assert all(
        mesh_validation(body)["watertight"]
        for panel in fabrication.panels for body in panel.bodies
    )
    assert all(
        panel.body("grout-thinset").bounds_mm[2] == 1.5
        and panel.body("grout-thinset").bounds_mm[5] == 2.5
        for panel in fabrication.panels
    )
    exported_tile_ids = [
        tile_id for panel in fabrication.panels for body in panel.bodies
        if body.material_channel_id.startswith("tile-color-")
        for tile_id in body.tile_ids
    ]
    assert sorted(exported_tile_ids) == sorted(tile.tile_id for tile in model.tiles)


def test_24_inch_square_escalates_from_3_by_3_to_the_minimum_valid_grid():
    model = resolve_designer_project(
        DesignerProjectShell.create("square", "l"), PRODUCTION_PROFILE,
    )
    plan = panelize_model(model)
    assert (plan.theoretical_rows, plan.theoretical_columns) == (3, 3)
    assert (plan.rows, plan.columns, len(plan.panels)) == (4, 3, 12)
    assert plan.attempted_layouts == ((3, 3), (3, 4), (4, 3))
    assert max(panel.width_mm for panel in plan.panels) <= 210.0
    assert max(panel.height_mm for panel in plan.panels) <= 210.0


def test_24_by_36_portrait_uses_the_minimum_5_by_3_grid():
    model = resolve_designer_project(
        DesignerProjectShell.create("portrait", "l"), PRODUCTION_PROFILE,
    )
    plan = panelize_model(model)
    assert (plan.theoretical_rows, plan.theoretical_columns) == (5, 3)
    assert (plan.rows, plan.columns, len(plan.panels)) == (5, 3, 15)
    assert [panel.panel_id for panel in plan.panels[:4]] == ["A1", "A2", "A3", "B1"]
    assert plan.attempted_layouts == ((5, 3),)
    assert max(panel.width_mm for panel in plan.panels) <= 210.0
    assert max(panel.height_mm for panel in plan.panels) <= 210.0


def test_point_and_flat_top_panelization_are_both_supported():
    point = panelize_model(_model(8, 7, "point_top"))
    flat = panelize_model(_model(7, 8, "flat_top"))
    assert point.model.tile_orientation == "point_top"
    assert flat.model.tile_orientation == "flat_top"
    assert all(panel.fits_safe_envelope for panel in point.panels + flat.panels)


def test_failure_is_clear_when_even_one_parent_cell_cannot_fit():
    with pytest.raises(PanelizationError, match="No tiles were cut") as error:
        panelize_model(build_phase2a_model(), safe_envelope_mm=(5.0, 5.0), maximum_extra_panels=2)
    assert "Attempted layouts" in str(error.value)
    assert "Largest parent cell" in str(error.value)


def test_panelization_and_package_regeneration_are_deterministic(tmp_path):
    model = build_phase2a_model()
    assert panelize_model(model) == panelize_model(model)
    first = generate_panelization_package(model, tmp_path / "first")
    second = generate_panelization_package(model, tmp_path / "second")
    assert first.geometry_signature == second.geometry_signature
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    first_files = {path.relative_to(first.output_directory): path.read_bytes() for path in first.stl_paths}
    second_files = {path.relative_to(second.output_directory): path.read_bytes() for path in second.stl_paths}
    assert first_files == second_files
    manifest = json.loads(first.manifest_path.read_text())
    assert manifest["tile_assignment"] == {
        "all_tiles_assigned_exactly_once": True, "tile_cuts_created": 0,
    }
    assert manifest["panel_connection"]["dedicated_connector_geometry"] is False
    assert all(record["stl_round_trip_valid"] for record in manifest["body_channel_ownership"])
    assert all(panel["backside_marking"]["content"] == panel["panel_id"] for panel in manifest["panels"])
    assert "TOP" not in json.dumps(manifest["panels"])

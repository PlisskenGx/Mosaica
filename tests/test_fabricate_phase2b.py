import json

from mosaica.fabricate import mesh_validation, rounded_tile_rings
from mosaica.fabricate.phase2a import PHASE2A_PROFILE, build_phase2a_prototype
from mosaica.fabricate.phase2b import (
    LEGACY_5_4_PROFILE,
    PANEL_ID_CELL_MM,
    PANEL_ID_DEBOSS_DEPTH_MM,
    PRODUCTION_PROFILE,
    build_production_prototype,
    generate_production_review_package,
    panel_identifier,
)


def test_production_profile_locks_the_physically_validated_z_stack():
    phase2a = build_phase2a_prototype()
    production = build_production_prototype()
    assert PRODUCTION_PROFILE.base_thickness_mm == 1.5
    assert PRODUCTION_PROFILE.grout_thickness_mm == 1.0
    assert PRODUCTION_PROFILE.straight_tile_relief_mm == 1.3
    assert PHASE2A_PROFILE.rounded_crown_mm == PRODUCTION_PROFILE.rounded_crown_mm == 0.8
    assert PRODUCTION_PROFILE.total_tile_relief_mm == 2.1
    assert production.model.physical_bounds_mm[-1] == 4.6
    assert LEGACY_5_4_PROFILE.base_thickness_mm == 2.0
    assert LEGACY_5_4_PROFILE.straight_tile_relief_mm == 1.6
    assert phase2a.model.profile == PHASE2A_PROFILE


def test_production_crown_preserves_validated_xy_and_translates_only_in_z():
    phase2a = build_phase2a_prototype()
    production = build_production_prototype()
    polygon = phase2a.model.tiles[0].full_polygon_mm
    before = rounded_tile_rings(
        polygon, 3.0, PHASE2A_PROFILE.straight_tile_relief_mm,
        PHASE2A_PROFILE.rounded_crown_mm, PHASE2A_PROFILE.crown_segments,
    )
    after = rounded_tile_rings(
        polygon, 2.5, PRODUCTION_PROFILE.straight_tile_relief_mm,
        PRODUCTION_PROFILE.rounded_crown_mm, PRODUCTION_PROFILE.crown_segments,
    )
    assert len(before) == len(after)
    for before_ring, after_ring in zip(before[2:], after[2:]):
        assert tuple((x, y) for x, y, _z in before_ring) == tuple(
            (x, y) for x, y, _z in after_ring
        )
        assert {
            round(before_point[2] - after_point[2], 9)
            for before_point, after_point in zip(before_ring, after_ring)
        } == {0.8}


def test_production_preserves_the_structural_and_grout_dimensions():
    prototype = build_production_prototype()
    assert prototype.model.profile.base_thickness_mm == 1.5
    assert prototype.model.profile.grout_thickness_mm == 1.0
    assert prototype.model.grout_gap_mm == 1.8
    assert prototype.model.profile.grout_depression_mm == 0.30
    for panel in prototype.panels:
        assert panel.body("grout-thinset").bounds_mm[2:6:3] == (1.5, 2.5)


def test_production_keeps_the_phase2a_seam_and_whole_tile_ownership():
    before = build_phase2a_prototype()
    after = build_production_prototype()
    assert after.seam == before.seam
    assert len(after.seam.points_mm) == 14
    assert {owner for _tile, owner in after.tile_ownership} == {"A1", "A2"}
    assert len(after.tile_ownership) == len(after.model.tiles)


def test_panel_ids_are_deterministic_and_support_multiple_rows():
    assert [panel_identifier(row, column) for row in range(3) for column in range(3)] == [
        "A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3",
    ]
    assert panel_identifier(26, 0) == "AA1"


def test_backside_marks_are_shallow_inside_watertight_base_bodies():
    prototype = build_production_prototype()
    assert PANEL_ID_CELL_MM == 1.0
    assert PANEL_ID_DEBOSS_DEPTH_MM == 0.35
    for panel in prototype.panels:
        body = panel.body("base")
        z_values = {point[2] for triangle in body.triangles for point in triangle}
        assert z_values == {0.0, PANEL_ID_DEBOSS_DEPTH_MM, 1.5}
        validation = mesh_validation(body)
        assert validation["watertight"] is True
        assert validation["nonmanifold_edges"] == 0
        assert validation["winding_errors"] == 0


def test_backside_marking_cells_stay_inside_their_panel_and_away_from_seam():
    prototype = build_production_prototype()
    for panel_id, cells in prototype.marking_cells:
        panel = next(value for value in prototype.panels if value.panel_id == panel_id)
        left, top, right, bottom = panel.fabrication_bounds_mm
        for cell in cells:
            assert all(left < x < right and top < y < bottom for x, y in cell)
            assert all(
                abs(x - prototype.seam.x_at(y)) >= 8.0
                for x, y in cell
            )


def test_backside_id_geometry_is_locally_mirrored_for_rear_reading_and_contains_no_arrow():
    prototype = build_production_prototype()
    cells = dict(prototype.marking_cells)
    assert {panel_id: len(values) for panel_id, values in cells.items()} == {
        "A1": 28, "A2": 34,
    }
    a1 = cells["A1"]
    origin_x = min(x for cell in a1 for x, _y in cell)
    origin_y = min(y for cell in a1 for _x, y in cell)
    first_row_offsets = {
        round(min(x for x, _y in cell) - origin_x, 2)
        for cell in a1 if min(y for _x, y in cell) == origin_y
    }
    # In front artwork coordinates the leading A occupies the right side of
    # the mark. Turning the panel over makes the physical rear read "A1".
    assert {8.25, 9.7, 11.15}.issubset(first_row_offsets)
    assert {2.9, 4.35}.isdisjoint(first_row_offsets)


def test_production_all_exported_bodies_are_manifold_and_deterministic():
    first = build_production_prototype()
    second = build_production_prototype()
    assert first == second
    assert all(
        mesh_validation(body)["watertight"]
        for panel in first.panels for body in panel.bodies
    )


def test_production_package_names_identity_and_stl_round_trip_agree(tmp_path):
    package = generate_production_review_package(tmp_path / "production")
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["application_version"] == "2.0.0"
    assert [panel["panel_id"] for panel in manifest["panels"]] == ["A1", "A2"]
    assert [(panel["row"], panel["column"]) for panel in manifest["panels"]] == [(0, 0), (0, 1)]
    assert all(panel["top_orientation"] == "artwork_top" for panel in manifest["panels"])
    assert all(panel["backside_marking"]["content"] == panel["panel_id"] for panel in manifest["panels"])
    assert all(panel["backside_marking"]["mirrored"] is True for panel in manifest["panels"])
    assert all(
        panel["backside_marking"]["coordinate_scope"] == "glyph_only"
        for panel in manifest["panels"]
    )
    assert all(
        panel["backside_marking"]["reading_direction"]
        == "left_to_right_when_viewed_from_backside"
        for panel in manifest["panels"]
    )
    assert "TOP" not in json.dumps(manifest["panels"])
    assert manifest["panel_connection"] == {
        "type": "natural_grout_line_seam",
        "dedicated_connector_geometry": False,
        "tile_cuts_created": 0,
        "permanent_structure": "ACP_backer_and_adhesive",
    }
    filenames = {path.name for path in package.stl_paths}
    assert "Panel_A1_Base.stl" in filenames
    assert "Panel_A2_Base.stl" in filenames
    assert all(record["owner"] in record["filename"] for record in manifest["body_channel_ownership"])
    assert all(record["stl_round_trip"]["valid"] for record in manifest["body_channel_ownership"])
    assert not any("connector" in filename.lower() for filename in filenames)


def test_production_manifest_records_validated_dimensions_and_process_metadata(tmp_path):
    package = generate_production_review_package(tmp_path / "production")
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["validated_dimensions"] == {
        "backside_deboss_depth_mm": 0.35,
        "finished_total_z_mm": 4.6,
        "panel_id_cell_mm": 1.0,
        "rounded_crown_mm": 0.8,
        "straight_tile_relief_mm": 1.3,
        "total_tile_relief_mm": 2.1,
    }
    assert manifest["provisionally_retained_dimensions"] == {
        "grout_depression_mm": 0.3,
        "grout_gap_mm": 1.8,
    }
    assert manifest["process_candidate"]["adaptive_variable_layer_height"] is True
    assert manifest["process_candidate"]["default_surface_finish"] == "Standard / no ironing"
    assert manifest["process_candidate"]["ironing"] == "optional"
    assert manifest["process_candidate"]["premium_ironing_profile"] == {
        "surface": "Topmost surfaces",
        "pattern": "Concentric",
        "flow_percent": 18,
        "speed_mm_s": 30,
        "line_spacing_mm": 0.15,
    }

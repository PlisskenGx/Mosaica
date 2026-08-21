import json

from mosaica.fabricate import mesh_validation, rounded_tile_rings
from mosaica.fabricate.export import parse_ascii_stl
from mosaica.fabricate.mesh import MeshBody, concave_grout_spatial_validation
from mosaica.fabricate.panelize import (
    P1S_V1_SAFE_ENVELOPE_MM,
    build_panelized_fabrication,
    panelize_model,
)
from mosaica.fabricate.phase2b import (
    LEGACY_5_4_PROFILE,
    PANEL_ID_CELL_MM,
    PANEL_ID_DEBOSS_DEPTH_MM,
    PRODUCTION_PROFILE,
    build_production_prototype,
    generate_production_review_package,
)


def test_approved_thin_stack_is_the_single_active_production_profile():
    assert (
        PRODUCTION_PROFILE.base_thickness_mm,
        PRODUCTION_PROFILE.grout_thickness_mm,
        PRODUCTION_PROFILE.straight_tile_relief_mm,
        PRODUCTION_PROFILE.rounded_crown_mm,
        PRODUCTION_PROFILE.total_tile_relief_mm,
    ) == (1.5, 1.0, 1.3, 0.8, 2.1)
    assert build_production_prototype().model.physical_bounds_mm[-1] == 4.6
    assert (
        LEGACY_5_4_PROFILE.base_thickness_mm,
        LEGACY_5_4_PROFILE.grout_thickness_mm,
        LEGACY_5_4_PROFILE.straight_tile_relief_mm,
        LEGACY_5_4_PROFILE.rounded_crown_mm,
        LEGACY_5_4_PROFILE.total_tile_relief_mm,
    ) == (2.0, 1.0, 1.6, 0.8, 2.4)


def test_production_crown_is_the_legacy_crown_translated_in_z_only():
    legacy = build_production_prototype(LEGACY_5_4_PROFILE)
    production = build_production_prototype()
    assert [tile.polygon_mm for tile in legacy.model.tiles] == [
        tile.polygon_mm for tile in production.model.tiles
    ]
    polygon = production.model.tiles[0].full_polygon_mm
    legacy_rings = rounded_tile_rings(polygon, 3.0, 1.6, 0.8, 6)
    production_rings = rounded_tile_rings(polygon, 2.5, 1.3, 0.8, 6)
    for before, after in zip(legacy_rings[2:], production_rings[2:]):
        assert [(x, y) for x, y, _z in before] == [(x, y) for x, y, _z in after]
        assert {round(before_z - after_z, 9) for (*_xy, before_z), (*_ab, after_z) in zip(before, after)} == {0.8}
    assert round(legacy_rings[-1][0][2] - legacy_rings[1][0][2], 9) == 0.8
    assert round(production_rings[-1][0][2] - production_rings[1][0][2], 9) == 0.8


def test_production_base_xy_and_backside_marking_remain_valid_and_watertight():
    legacy = build_production_prototype(LEGACY_5_4_PROFILE)
    production = build_production_prototype()
    assert PANEL_ID_CELL_MM == 1.0
    assert PANEL_ID_DEBOSS_DEPTH_MM == 0.35
    for before, after in zip(legacy.panels, production.panels):
        before_base, after_base = before.body("base"), after.body("base")
        assert {
            (point[0], point[1])
            for triangle in before_base.triangles for point in triangle
        } == {
            (point[0], point[1])
            for triangle in after_base.triangles for point in triangle
        }
        z_values = {point[2] for triangle in after_base.triangles for point in triangle}
        assert z_values == {0.0, 0.35, 1.5}
        assert PRODUCTION_PROFILE.base_thickness_mm - PANEL_ID_DEBOSS_DEPTH_MM == 1.15
        assert mesh_validation(after_base)["watertight"] is True


def test_production_grout_datums_concavity_and_finished_height_are_exact():
    prototype = build_production_prototype()
    assert prototype.model.grout_gap_mm == 1.8
    assert prototype.model.profile.grout_depression_mm == 0.30
    assert prototype.model.physical_bounds_mm[-1] == 4.6
    ownership = dict(prototype.tile_ownership)
    for panel in prototype.panels:
        grout = panel.body("grout-thinset")
        assert grout.bounds_mm[2] == 1.5
        assert grout.bounds_mm[5] == 2.5
        tiles = tuple(
            tile for tile in prototype.model.tiles
            if ownership[tile.tile_id] == panel.panel_id
        )
        validation = concave_grout_spatial_validation(
            prototype.model, grout.triangles, tiles=tiles,
        )
        assert validation["maximum_depression_mm"] == 0.3
        assert min(point[2] for triangle in grout.triangles for point in triangle) == 1.5
        assert any(
            point[2] == 2.2 for triangle in grout.triangles for point in triangle
        )
        assert max(
            body.bounds_mm[5] for body in panel.bodies
            if body.material_channel_id.startswith("tile-color-")
        ) == 4.6


def test_production_profile_remains_compatible_with_unchanged_panelization():
    prototype = build_production_prototype()
    plan = panelize_model(prototype.model)
    assert P1S_V1_SAFE_ENVELOPE_MM == (210.0, 210.0)
    assert [panel.panel_id for panel in plan.panels] == ["A1"]
    assert len(plan.tile_ownership) == len(prototype.model.tiles)
    assert len({tile_id for tile_id, _owner in plan.tile_ownership}) == len(prototype.model.tiles)
    fabrication = build_panelized_fabrication(plan)
    assert all(
        mesh_validation(body)["watertight"]
        for panel in fabrication.panels for body in panel.bodies
    )


def test_production_review_package_is_deterministic_and_round_trips(tmp_path):
    first = generate_production_review_package(tmp_path / "first")
    second = generate_production_review_package(tmp_path / "second")
    assert first.geometry_signature == second.geometry_signature
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    manifest = json.loads(first.manifest_path.read_text())
    assert manifest["fabrication_profile"] == PRODUCTION_PROFILE.__dict__
    assert manifest["validated_dimensions"]["finished_total_z_mm"] == 4.6
    assert manifest["panel_connection"]["tile_cuts_created"] == 0
    assert manifest["panel_connection"]["dedicated_connector_geometry"] is False
    assert manifest["process_candidate"]["adaptive_variable_layer_height"] is True
    assert manifest["process_candidate"]["default_surface_finish"] == "Standard / no ironing"
    assert manifest["process_candidate"]["ironing"] == "optional"
    for path in first.stl_paths:
        triangles = parse_ascii_stl(path)
        assert mesh_validation(MeshBody("round-trip", path.stem, "test", triangles))["watertight"]
    assert {
        path.relative_to(first.output_directory): path.read_bytes()
        for path in first.stl_paths
    } == {
        path.relative_to(second.output_directory): path.read_bytes()
        for path in second.stl_paths
    }

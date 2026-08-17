import json
from dataclasses import replace
from math import isclose

from mosaica.fabricate import (
    build_single_panel_geometry,
    clip_mesh_to_fabrication_perimeter,
    concave_grout_spatial_validation,
    extruded_polygon_mesh,
    mesh_validation,
)
from mosaica.fabricate.mesh import _point_segment_distance
from mosaica.fabricate.phase2a import (
    PHASE2A_PROFILE,
    build_phase2a_model,
    build_phase2a_prototype,
    generate_phase2a_review_package,
)
from mosaica.fabricate.review import build_review_model, build_review_panel


EXPECTED_SEAM = (
    (65.4, 0.0), (65.4, 0.519615), (76.3, 6.812733),
    (76.3, 19.398969), (65.4, 25.692087), (65.4, 38.278323),
    (76.3, 44.571441), (76.3, 57.157677), (65.4, 63.450795),
    (65.4, 76.03703), (76.3, 82.330148), (76.3, 94.916384),
    (65.4, 101.209502), (65.4, 101.72911743121205),
)


def _tile_solids(panel):
    result = {}
    for body in panel.bodies:
        if not body.material_channel_id.startswith("tile-color-"):
            continue
        start = 0
        for tile_id, count in zip(body.tile_ids, body.solid_triangle_counts):
            result[tile_id] = body.triangles[start:start + count]
            start += count
    return result


def test_flat_grout_branch_remains_the_original_extruded_geometry():
    model = build_review_model()
    panel = build_single_panel_geometry(model)
    rectangle = (
        (0.0, 0.0), (model.artwork_width_mm, 0.0),
        (model.artwork_width_mm, model.artwork_height_mm),
        (0.0, model.artwork_height_mm),
    )
    expected = clip_mesh_to_fabrication_perimeter(
        extruded_polygon_mesh(rectangle, 2.0, 3.0), model,
    )
    assert panel.body("grout-thinset").triangles == expected
    assert model.profile.grout_surface == "flat"
    assert model.profile.grout_depression_mm == 0.0


def test_concave_grout_has_exact_shallow_depression_and_is_watertight():
    prototype = build_phase2a_prototype()
    assert PHASE2A_PROFILE.grout_depression_mm == 0.30
    for panel in prototype.panels:
        body = panel.body("grout-thinset")
        exposed_z = {
            point[2] for triangle in body.triangles for point in triangle
            if point[2] > PHASE2A_PROFILE.base_thickness_mm
        }
        assert min(exposed_z) == 2.70
        assert max(exposed_z) == 3.0
        assert mesh_validation(body)["watertight"] is True


def test_edge_driven_grout_is_orientation_independent_and_exact():
    prototype = build_phase2a_prototype()
    ownership = dict(prototype.tile_ownership)
    assert PHASE2A_PROFILE.grout_mesh_step_mm == 0.30
    for panel in prototype.panels:
        tiles = tuple(
            tile for tile in prototype.model.tiles
            if ownership[tile.tile_id] == panel.panel_id
        )
        validation = concave_grout_spatial_validation(
            prototype.model, panel.body("grout-thinset").triangles,
            tiles=tiles,
        )
        assert all(
            count > 0 for count in
            validation["boundary_edge_count_by_orientation"].values()
        )
        assert validation["boundary_z_range_by_orientation"] == {
            "horizontal": (3.0, 3.0),
            "+60": (3.0, 3.0),
            "-60": (3.0, 3.0),
        }
        assert validation["maximum_boundary_deviation_mm"] <= 1e-8
        assert validation["maximum_depression_mm"] == 0.30
        assert validation["transverse_segments_per_half_gap"] == 3
        assert validation["depressed_vertices_inside_tiles"] == 0


def test_edge_driven_grout_has_smooth_shared_triple_junctions():
    prototype = build_phase2a_prototype()
    ownership = dict(prototype.tile_ownership)
    for panel in prototype.panels:
        tiles = tuple(
            tile for tile in prototype.model.tiles
            if ownership[tile.tile_id] == panel.panel_id
        )
        validation = concave_grout_spatial_validation(
            prototype.model, panel.body("grout-thinset").triangles,
            tiles=tiles,
        )
        assert validation["triple_junction_count"] > 0
        assert validation["maximum_triple_junction_z_spread_mm"] == 0.0


def test_concavity_preserves_grout_width_and_all_tile_geometry():
    prototype = build_phase2a_prototype()
    assert prototype.model.grout_gap_mm == 1.8
    original = _tile_solids(build_review_panel())
    panelized = {}
    for panel in prototype.panels:
        panelized.update(_tile_solids(panel))
    assert panelized == original
    assert prototype.model.tile_flat_to_flat_mm == build_review_model().tile_flat_to_flat_mm


def test_seam_is_one_continuous_grout_path_and_never_cuts_a_tile():
    prototype = build_phase2a_prototype()
    seam = prototype.seam
    assert seam.orientation == "vertical-grout-line"
    assert seam.points_mm == EXPECTED_SEAM
    assert seam.points_mm[0][1] == 0.0
    assert seam.points_mm[-1][1] == prototype.model.artwork_height_mm
    for point in seam.points_mm:
        distance = min(
            _point_segment_distance(point, first, second)
            for tile in prototype.model.tiles
            for first, second in zip(
                tile.polygon_mm, tile.polygon_mm[1:] + tile.polygon_mm[:1],
            )
        )
        assert distance >= prototype.model.grout_gap_mm / 2.0 - 1e-6


def test_panelization_assigns_every_source_tile_whole_and_exactly_once():
    prototype = build_phase2a_prototype()
    ownership = dict(prototype.tile_ownership)
    assert set(ownership) == {tile.tile_id for tile in prototype.model.tiles}
    assert set(ownership.values()) == {"A", "B"}
    panel_ids = [
        tile_id
        for panel in prototype.panels
        for body in panel.bodies if body.material_channel_id.startswith("tile-color-")
        for tile_id in body.tile_ids
    ]
    assert len(panel_ids) == len(set(panel_ids)) == len(prototype.model.tiles)


def test_reconstructed_panels_preserve_outer_bounds_and_normal_seam_grout():
    prototype = build_phase2a_prototype()
    left, top, right, bottom = (
        0.9, 0.0, 129.9, prototype.model.artwork_height_mm,
    )
    assert min(panel.fabrication_bounds_mm[0] for panel in prototype.panels) == left
    assert max(panel.fabrication_bounds_mm[2] for panel in prototype.panels) == right
    assert min(panel.fabrication_bounds_mm[1] for panel in prototype.panels) == top
    assert max(panel.fabrication_bounds_mm[3] for panel in prototype.panels) == bottom
    assert prototype.model.grout_gap_mm == 1.8


def test_natural_seam_is_the_only_panel_connection_geometry():
    prototype = build_phase2a_prototype()
    assert not hasattr(prototype, "locators")
    for panel in prototype.panels:
        assert panel.body("base").bounds_mm[5] == 2.0
        assert panel.body("grout-thinset").bounds_mm[2] == 2.0
        tile_bodies = [
            body for body in panel.bodies
            if body.material_channel_id.startswith("tile-color-")
        ]
        assert min(body.bounds_mm[2] for body in tile_bodies) == 3.0


def test_base_ownership_meets_exactly_at_natural_seam_without_gap_or_overlap():
    prototype = build_phase2a_prototype()
    base_vertices = []
    for panel in prototype.panels:
        base_vertices.append({
            (round(x, 6), round(y, 6))
            for triangle in panel.body("base").triangles
            for x, y, _ in triangle
        })
    for x, y in prototype.seam.points_mm:
        point = round(x, 6), round(y, 6)
        assert point in base_vertices[0]
        assert point in base_vertices[1]
    # The same seam function is the right boundary of A and left boundary of B;
    # their combined X extent is the original fabrication rectangle.
    assert prototype.panels[0].body("base").bounds_mm[0] == 0.9
    assert prototype.panels[1].body("base").bounds_mm[3] == 129.9


def test_visible_grout_panel_edges_are_complementary_at_the_unchanged_seam():
    prototype = build_phase2a_prototype()
    grout_vertices = []
    for panel in prototype.panels:
        grout_vertices.append({
            (round(x, 6), round(y, 6))
            for triangle in panel.body("grout-thinset").triangles
            for x, y, _ in triangle
        })
    for x, y in prototype.seam.points_mm:
        point = round(x, 6), round(y, 6)
        assert point in grout_vertices[0]
        assert point in grout_vertices[1]


def test_panel_bodies_are_watertight_and_output_is_deterministic():
    first = build_phase2a_prototype()
    second = build_phase2a_prototype()
    assert first == second
    assert all(
        mesh_validation(body)["watertight"]
        for panel in first.panels for body in panel.bodies
    )


def test_phase2a_does_not_mutate_or_redefine_the_flat_review_model():
    before = build_review_model()
    build_phase2a_prototype()
    after = build_review_model()
    assert before == after
    assert before.profile.grout_surface == "flat"
    assert replace(before.profile) == after.profile


def test_phase2a_package_records_geometry_and_passes_stl_round_trip(tmp_path):
    package = generate_phase2a_review_package(tmp_path / "phase2a")
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["application_version"] == "2.0.0"
    assert manifest["grout_mode"] == "concave"
    assert manifest["grout_depression_mm"] == 0.30
    assert manifest["seam"]["tile_cuts_created"] == 0
    assert "connector" not in manifest
    assert manifest["panel_connection"] == {
        "type": "natural_grout_line_seam",
        "dedicated_connector_geometry": False,
        "tile_cuts_created": 0,
        "physical_test_purpose": (
            "evaluate natural seam registration before adopting any "
            "dedicated alignment or retention geometry"
        ),
        "permanent_structure": "ACP_backer_and_adhesive",
    }
    assert len(package.stl_paths) == 9
    assert not any("Calibration" in path.name for path in package.stl_paths)
    assert all(
        record["mesh_validation"]["watertight"]
        and record["stl_round_trip"]["valid"]
        for record in manifest["body_channel_ownership"]
    )
    grout_records = [
        record for record in manifest["body_channel_ownership"]
        if record["logical_channel"] == "grout-thinset"
    ]
    assert all(
        record["concave_surface_validation"]["maximum_boundary_deviation_mm"]
        <= 1e-8
        for record in grout_records
    )
    assert len(package.geometry_signature) == 64

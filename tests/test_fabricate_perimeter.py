from math import isclose

from mosaica.designer import DesignerProjectShell
from mosaica.fabricate import (
    MeshBody,
    build_single_panel_geometry,
    clip_mesh_to_axis_plane,
    fabrication_perimeter_bounds,
    mesh_validation,
    resolve_designer_project,
    rounded_tile_mesh,
)
from mosaica.fabricate.review import REVIEW_PROFILE, build_review_model, build_review_panel


def _solid(panel, tile_id):
    tile = next(value for value in panel.model.tiles if value.tile_id == tile_id)
    body = panel.body(tile.material_channel_id)
    index = body.tile_ids.index(tile_id)
    start = sum(body.solid_triangle_counts[:index])
    count = body.solid_triangle_counts[index]
    return tile, body.triangles[start:start + count]


def _bounds(triangles):
    points = [point for triangle in triangles for point in triangle]
    return (
        min(value[0] for value in points), min(value[1] for value in points),
        min(value[2] for value in points), max(value[0] for value in points),
        max(value[1] for value in points), max(value[2] for value in points),
    )


def _assert_vertical_cut_face(triangles, axis, plane):
    cap_faces = [
        triangle for triangle in triangles
        if all(isclose(point[axis], plane, abs_tol=1e-9) for point in triangle)
    ]
    assert cap_faces
    points = [point for triangle in cap_faces for point in triangle]
    assert min(point[2] for point in points) == 3.0
    assert max(point[2] for point in points) == 5.4


def _assert_original_crown_survives(tile, fabricated, bounds):
    original = rounded_tile_mesh(
        tile.full_polygon_mm, 3.0, 1.6, 0.8, REVIEW_PROFILE.crown_segments,
    )
    left, top, right, bottom = bounds
    original_crown_vertices = {
        point for triangle in original for point in triangle
        if left - 1e-7 <= point[0] <= right + 1e-7
        and top - 1e-7 <= point[1] <= bottom + 1e-7
        and point[2] > 4.6
    }
    fabricated_vertices = {point for triangle in fabricated for point in triangle}
    assert original_crown_vertices
    assert original_crown_vertices & fabricated_vertices


def test_point_top_uses_neighboring_full_tile_left_and_right_planes():
    panel = build_review_panel()
    assert panel.model.grout_gap_mm == 1.8
    assert panel.fabrication_bounds_mm == (0.9, 0.0, 129.9, panel.model.artwork_height_mm)
    left = next(value for value in panel.model.tiles if min(x for x, _ in value.polygon_mm) == 0.0)
    right = next(
        value for value in panel.model.tiles
        if isclose(max(x for x, _ in value.polygon_mm), panel.model.artwork_width_mm)
    )
    assert _bounds(_solid(panel, left.tile_id)[1])[0] == 0.9
    assert _bounds(_solid(panel, right.tile_id)[1])[3] == 129.9
    assert panel.body("base").bounds_mm[0:4:3] == (0.9, 129.9)
    assert panel.body("grout-thinset").bounds_mm[0:4:3] == (0.9, 129.9)


def test_point_top_neighboring_full_tiles_and_centers_do_not_move():
    model = build_review_model()
    panel = build_single_panel_geometry(model)
    left_full = next(
        value for value in model.tiles
        if value.piece_type == "full"
        and isclose(min(x for x, _ in value.polygon_mm), 0.9)
    )
    right_full = next(
        value for value in model.tiles
        if value.piece_type == "full"
        and isclose(max(x for x, _ in value.polygon_mm), 129.9)
    )
    for tile in (left_full, right_full):
        _, fabricated = _solid(panel, tile.tile_id)
        assert _bounds(fabricated)[0] >= 0.9
        assert _bounds(fabricated)[3] <= 129.9
        assert next(value.center_mm for value in panel.model.tiles if value.tile_id == tile.tile_id) == tile.center_mm
    assert isclose(min(x for x, _ in left_full.polygon_mm), panel.fabrication_bounds_mm[0])
    assert isclose(panel.fabrication_bounds_mm[2], max(x for x, _ in right_full.polygon_mm))
    assert panel.model.grout_gap_mm == model.grout_gap_mm == 1.8


def test_point_top_all_bodies_share_straight_planes_and_remain_watertight():
    panel = build_review_panel()
    for body in panel.bodies:
        assert body.bounds_mm[0] >= 0.9
        assert body.bounds_mm[3] <= 129.9
        assert mesh_validation(body)["watertight"] is True
    assert panel.body("base").bounds_mm[0] == panel.body("grout-thinset").bounds_mm[0] == 0.9
    assert panel.body("base").bounds_mm[3] == panel.body("grout-thinset").bounds_mm[3] == 129.9


def test_flat_top_trims_top_and_bottom_only():
    shell = DesignerProjectShell.create_custom("s", "flat_top", 3, 3)
    model = resolve_designer_project(shell, REVIEW_PROFILE)
    panel = build_single_panel_geometry(model)
    assert fabrication_perimeter_bounds(model) == (
        0.0, 0.9, model.artwork_width_mm, round(model.artwork_height_mm - 0.9, 9),
    )
    assert panel.body("base").bounds_mm[0] == 0.0
    assert panel.body("base").bounds_mm[1] == 0.9
    assert isclose(panel.body("base").bounds_mm[3], model.artwork_width_mm, abs_tol=1e-9)
    assert panel.body("base").bounds_mm[4] == round(model.artwork_height_mm - 0.9, 9)
    top = next(value for value in model.tiles if min(y for _, y in value.polygon_mm) == 0.0)
    bottom = next(
        value for value in model.tiles
        if isclose(max(y for _, y in value.polygon_mm), model.artwork_height_mm)
    )
    assert _bounds(_solid(panel, top.tile_id)[1])[1] == 0.9
    assert _bounds(_solid(panel, bottom.tile_id)[1])[4] == round(model.artwork_height_mm - 0.9, 9)
    assert all(mesh_validation(value)["watertight"] for value in panel.bodies)


def test_designer_artwork_dimensions_remain_pretrim_and_lattice_is_unchanged():
    model = build_review_model()
    before = tuple((value.tile_id, value.center_mm, value.polygon_mm) for value in model.tiles)
    panel = build_single_panel_geometry(model)
    assert model.artwork_width_mm == 130.8
    assert panel.fabrication_bounds_mm[2] - panel.fabrication_bounds_mm[0] == 129.0
    assert tuple((value.tile_id, value.center_mm, value.polygon_mm) for value in model.tiles) == before


def test_interior_full_tile_retains_unchanged_v4_crown():
    panel = build_review_panel()
    tile, fabricated = _solid(panel, "placement-000040")
    original = rounded_tile_mesh(
        tile.full_polygon_mm, 3.0, 1.6, 0.8, REVIEW_PROFILE.crown_segments,
    )
    assert fabricated == original


def test_left_and_right_artificial_edges_are_full_height_straight_cuts():
    panel = build_review_panel()
    left_tile = next(
        value for value in panel.model.tiles
        if min(x for x, _ in value.full_polygon_mm) < 0.9 - 1e-8
    )
    right_tile = next(
        value for value in panel.model.tiles
        if max(x for x, _ in value.full_polygon_mm) > 129.9 + 1e-8
    )
    for tile, axis, plane in ((left_tile, 0, 0.9), (right_tile, 0, 129.9)):
        _, triangles = _solid(panel, tile.tile_id)
        _assert_vertical_cut_face(triangles, axis, plane)
        _assert_original_crown_survives(tile, triangles, panel.fabrication_bounds_mm)


def test_top_and_bottom_artificial_edges_are_full_height_straight_cuts():
    panel = build_review_panel()
    top_tile = next(
        value for value in panel.model.tiles
        if min(y for _, y in value.full_polygon_mm) < -1e-8
    )
    bottom_tile = next(
        value for value in panel.model.tiles
        if max(y for _, y in value.full_polygon_mm) > panel.model.artwork_height_mm + 1e-8
    )
    for tile, plane in ((top_tile, 0.0), (bottom_tile, panel.model.artwork_height_mm)):
        _, triangles = _solid(panel, tile.tile_id)
        _assert_vertical_cut_face(triangles, 1, plane)
        _assert_original_crown_survives(tile, triangles, panel.fabrication_bounds_mm)


def test_corner_clip_has_two_straight_cut_faces_and_preserves_genuine_crown():
    # A standalone parent hex crossing perpendicular artwork planes exercises
    # the same final solid-clipping path as a future corner placement.
    parent = (
        (-10.0, 0.0), (-5.0, 8.660254038), (5.0, 8.660254038),
        (10.0, 0.0), (5.0, -8.660254038), (-5.0, -8.660254038),
    )
    original = rounded_tile_mesh(parent, 3.0, 1.6, 0.8, REVIEW_PROFILE.crown_segments)
    clipped = clip_mesh_to_axis_plane(original, axis=0, plane_mm=0.0, keep_greater=True)
    clipped = clip_mesh_to_axis_plane(clipped, axis=1, plane_mm=0.0, keep_greater=True)
    _assert_vertical_cut_face(clipped, 0, 0.0)
    _assert_vertical_cut_face(clipped, 1, 0.0)
    assert mesh_validation(MeshBody(
        "corner", "Corner", "tile-color-1", clipped,
        ("corner",), (len(clipped),),
    ))["watertight"] is True
    original_crown_vertices = {
        point for triangle in original for point in triangle
        if point[0] >= 0.0 and point[1] >= 0.0 and point[2] > 4.6
    }
    clipped_vertices = {point for triangle in clipped for point in triangle}
    assert original_crown_vertices & clipped_vertices

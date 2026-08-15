from math import isclose

from mosaic_engine.fabricate import (
    MeshBody,
    maximum_triangle_edge,
    mesh_validation,
    parse_ascii_stl,
    polygon_diameter,
    rounded_tile_mesh,
    rounded_tile_rings,
    tile_body_spatial_validation,
    write_mesh_stl,
)
from mosaic_engine.fabricate.review import REVIEW_PROFILE, build_review_model, build_review_panel


GROUT_TOP_MM = REVIEW_PROFILE.base_thickness_mm + REVIEW_PROFILE.grout_thickness_mm


def _bounds(triangles):
    points = [point for triangle in triangles for point in triangle]
    return (
        min(value[0] for value in points), min(value[1] for value in points),
        min(value[2] for value in points), max(value[0] for value in points),
        max(value[1] for value in points), max(value[2] for value in points),
    )


def _source_bounds(tile):
    return (
        min(value[0] for value in tile.polygon_mm),
        min(value[1] for value in tile.polygon_mm),
        max(value[0] for value in tile.polygon_mm),
        max(value[1] for value in tile.polygon_mm),
    )


def _one_tile_body(tile):
    triangles = rounded_tile_mesh(
        tile.polygon_mm, GROUT_TOP_MM,
        REVIEW_PROFILE.straight_tile_relief_mm,
        REVIEW_PROFILE.rounded_crown_mm,
        REVIEW_PROFILE.crown_segments,
    )
    return MeshBody(
        "one-tile", "One Tile", tile.material_channel_id,
        triangles, (tile.tile_id,), (len(triangles),),
    )


def test_one_full_tile_stays_in_its_authoritative_footprint():
    tile = next(
        value for value in build_review_model().tiles
        if value.tile_id == "placement-000040"
    )
    assert tile.piece_type == "full"
    body = _one_tile_body(tile)
    source = _source_bounds(tile)
    assert body.bounds_mm == (
        source[0], source[1], GROUT_TOP_MM,
        source[2], source[3], GROUT_TOP_MM + 2.4,
    )
    assert isclose(body.bounds_mm[3] - body.bounds_mm[0], 20.0)
    assert isclose(body.bounds_mm[4] - body.bounds_mm[1], 23.09401076758503)
    assert maximum_triangle_edge(body.triangles) <= polygon_diameter(tile.polygon_mm) * 1.01
    assert mesh_validation(body)["watertight"] is True
    spatial = tile_body_spatial_validation(body, (tile,), GROUT_TOP_MM, 2.4)
    assert spatial["valid"] is True
    assert spatial["connected_component_count"] == 1
    assert spatial["cross_tile_triangles"] == 0


def test_full_tile_crown_rings_preserve_center_and_vertex_count():
    tile = next(
        value for value in build_review_model().tiles
        if value.tile_id == "placement-000040"
    )
    rings = rounded_tile_rings(
        tile.polygon_mm, GROUT_TOP_MM, 1.6, 0.8,
        REVIEW_PROFILE.crown_segments,
    )
    assert len(rings) == 2 + REVIEW_PROFILE.crown_segments
    assert all(len(value) == 6 for value in rings)
    assert rings[0] == tuple((x, y, GROUT_TOP_MM) for x, y in tile.polygon_mm)
    assert rings[1] == tuple((x, y, GROUT_TOP_MM + 1.6) for x, y in tile.polygon_mm)
    expected_center = (
        sum(value[0] for value in tile.polygon_mm) / 6,
        sum(value[1] for value in tile.polygon_mm) / 6,
    )
    for ring in rings:
        center = (
            sum(value[0] for value in ring) / len(ring),
            sum(value[1] for value in ring) / len(ring),
        )
        assert isclose(center[0], expected_center[0], abs_tol=1e-9)
        assert isclose(center[1], expected_center[1], abs_tol=1e-9)


def test_one_clipped_tile_stays_in_clipped_visible_polygon():
    tile = next(value for value in build_review_model().tiles if value.piece_type != "full")
    body = _one_tile_body(tile)
    source = _source_bounds(tile)
    assert body.bounds_mm[:2] == source[:2]
    assert body.bounds_mm[3:5] == source[2:]
    assert body.bounds_mm[2] == GROUT_TOP_MM
    assert body.bounds_mm[5] == GROUT_TOP_MM + 2.4
    assert mesh_validation(body)["watertight"] is True
    assert tile_body_spatial_validation(body, (tile,), GROUT_TOP_MM, 2.4)["valid"] is True


def test_one_tile_stl_round_trip_preserves_geometry(tmp_path):
    tile = next(value for value in build_review_model().tiles if value.piece_type == "full")
    body = _one_tile_body(tile)
    path = write_mesh_stl(body, tmp_path / "one-tile.stl")
    parsed = parse_ascii_stl(path)
    parsed_body = MeshBody(
        body.body_id, body.name, body.material_channel_id,
        parsed, body.tile_ids, body.solid_triangle_counts,
    )
    assert len(parsed) == len(body.triangles)
    assert all(
        isclose(left, right, abs_tol=1e-8)
        for left, right in zip(parsed_body.bounds_mm, body.bounds_mm)
    )
    assert isclose(
        maximum_triangle_edge(parsed),
        maximum_triangle_edge(body.triangles), abs_tol=1e-9,
    )
    assert mesh_validation(parsed_body)["watertight"] is True
    assert tile_body_spatial_validation(
        parsed_body, (tile,), GROUT_TOP_MM, 2.4,
    )["valid"] is True


def test_multitile_color_bodies_have_one_shell_per_source_and_no_cross_tile_faces():
    panel = build_review_panel()
    for body in panel.bodies[2:]:
        tiles = tuple(
            value for value in panel.model.tiles
            if value.material_channel_id == body.material_channel_id
        )
        validation = tile_body_spatial_validation(
            body, tiles, GROUT_TOP_MM, 2.4,
        )
        assert validation["valid"] is True
        assert validation["connected_component_count"] == len(tiles)
        assert validation["source_tile_correspondence"] is True
        assert validation["cross_tile_triangles"] == 0
        assert all(value["valid"] for value in validation["shells"])


def test_tile_color_three_single_shell_has_local_not_artwork_bounds():
    panel = build_review_panel()
    body = panel.body("tile-color-3")
    assert body.tile_ids == ("placement-000040",)
    assert body.bounds_mm == (
        55.4, 39.317553331813514, 3.0,
        75.4, 62.41156409939854, 5.4,
    )
    assert maximum_triangle_edge(body.triangles) < 24.0

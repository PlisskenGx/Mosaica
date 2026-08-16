from math import ceil, hypot, isclose

import pytest

from mosaica.boundary import polygon_area
from mosaica.designer import CANVAS_PRESETS, TILE_PRESETS, DesignerProjectShell
from mosaica.designer import MosaicDesignerApp
from mosaica.border import build_border_layer


ORIENTATIONS = ("flat_top", "point_top")


def _request(app, method, path, body=None):
    from io import BytesIO
    import json

    raw = json.dumps(body).encode() if body is not None else b""
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    response = b"".join(app({
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": BytesIO(raw),
    }, start_response))
    return captured["status"], json.loads(response)


def _is_vertex(point, vertices):
    return any(
        isclose(point[0], vertex[0], abs_tol=1e-8)
        and isclose(point[1], vertex[1], abs_tol=1e-8)
        for vertex in vertices
    )


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_both_orientations_are_regular_and_preserve_flat_to_flat(orientation):
    shell = DesignerProjectShell.create("square-s", "m", orientation)
    tile = next(value for value in shell.geometry.placements if value.piece_type == "full")
    vertices = tile.full_vertices_in
    sides = [
        hypot(vertices[(index + 1) % 6][0] - point[0], vertices[(index + 1) % 6][1] - point[1])
        for index, point in enumerate(vertices)
    ]
    assert max(sides) - min(sides) < 1e-9
    if orientation == "point_top":
        across_flats = max(x for x, _ in vertices) - min(x for x, _ in vertices)
    else:
        across_flats = max(y for _, y in vertices) - min(y for _, y in vertices)
    assert isclose(across_flats, shell.tile.flat_to_flat_in, abs_tol=1e-9)


@pytest.mark.parametrize("canvas", CANVAS_PRESETS, ids=lambda value: value.id)
@pytest.mark.parametrize("tile", TILE_PRESETS, ids=lambda value: value.id)
@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_all_designer_panels_have_vertex_only_non_sliver_perimeters(
    canvas, tile, orientation,
):
    shell = DesignerProjectShell.create(canvas.id, tile.id, orientation)
    geometry = shell.geometry
    cuts = [
        value for value in geometry.placements
        if value.piece_type not in {"full", "outside"}
    ]
    assert cuts
    for placement in cuts:
        assert len(placement.vertices_in) >= 3
        assert polygon_area(placement.vertices_in) > 1e-6
        assert all(_is_vertex(point, placement.full_vertices_in) for point in placement.vertices_in)
        assert placement.piece_fraction >= 1 / 6 - 1e-8
        assert any(
            isclose(placement.piece_fraction, expected, abs_tol=1e-8)
            for expected in (1 / 6, 1 / 2)
        )
    assert sum(value.piece_type != "outside" for value in geometry.placements) == (
        shell.to_dict()["geometry"]["visible_piece_count"]
    )


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_nearest_extent_selection_is_deterministic_around_boundary(orientation):
    first = DesignerProjectShell.create("square-s", "m", orientation).geometry
    second = DesignerProjectShell.create("square-s", "m", orientation).geometry
    assert first == second
    assert abs(first.width_in - 24) < 0.7
    assert abs(first.height_in - 24) < 0.7


def test_rotating_orientation_swaps_actual_extent_axes_for_square_target():
    point = DesignerProjectShell.create("square-s", "m", "point_top").geometry
    flat = DesignerProjectShell.create("square-s", "m", "flat_top").geometry
    assert isclose(point.width_in, flat.height_in, abs_tol=1e-9)
    assert isclose(point.height_in, flat.width_in, abs_tol=1e-9)


def test_orientation_changes_geometry_and_placement_catalog():
    point = DesignerProjectShell.create("landscape", "m", "point_top")
    flat = DesignerProjectShell.create("landscape", "m", "flat_top")
    assert point.geometry.orientation == "point_top"
    assert flat.geometry.orientation == "flat_top"
    assert (point.geometry.columns, point.geometry.rows) != (
        flat.geometry.columns, flat.geometry.rows
    )


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_selected_orientation_reaches_workspace_api(orientation):
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {"shape": "hexagon"})
    status, payload = _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": orientation,
    })
    assert status == "200 OK"
    status, payload = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "square-s",
    })
    assert status == "200 OK"
    assert payload["stage"] == "workspace"
    assert payload["project"]["tile_orientation"] == orientation
    assert payload["project"]["geometry"]["orientation"] == orientation


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("preset", ("none", "solid", "double", "alternating"))
def test_border_topology_is_valid_for_both_orientations(orientation, preset):
    shell = DesignerProjectShell.create("square-s", "m", orientation)
    border = build_border_layer(shell.geometry, preset)
    visible = {
        f"placement-{index:06d}"
        for index, placement in enumerate(shell.geometry.placements)
        if placement.piece_type != "outside"
    }
    assert set(border.protected_placement_ids) <= visible
    assert set(border.available_artwork_placement_ids).isdisjoint(
        border.protected_placement_ids
    )


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_plate_estimate_uses_actual_dimensions(orientation):
    shell = DesignerProjectShell.create("square-s", "m", orientation)
    payload = shell.to_dict()
    expected_columns = ceil(shell.geometry.width_in * 25.4 / 256)
    expected_rows = ceil(shell.geometry.height_in * 25.4 / 256)
    assert payload["print_plate_estimate"]["columns"] == expected_columns
    assert payload["print_plate_estimate"]["rows"] == expected_rows

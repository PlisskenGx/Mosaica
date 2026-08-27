from io import BytesIO
import json
from pathlib import Path

from mosaica.designer import DesignerProjectShell, MosaicDesignerApp
from mosaica.engine import _point_in_polygon


def _request(app, method, path, body=None):
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


def _family_first_workspace(app, family, canvas_id="square"):
    started = _request(app, "POST", "/api/designer/new", {})[1]
    assert started["stage"] == "shape"
    orientation = "straight" if family == "square" else "point_top"
    sizes = _request(app, "POST", "/api/designer/shape", {
        "shape": family, "orientation": orientation,
    })[1]
    assert sizes["stage"] == "tile" and sizes["project"] is None
    canvases = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})[1]
    assert canvases["stage"] == "canvas" and canvases["project"] is None
    return _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": canvas_id,
    })[1]


def test_family_first_new_mosaic_reset_never_skips_family_size_or_canvas():
    app = MosaicDesignerApp()
    assert app.payload()["stage"] == "welcome"
    for first, second in (("hexagon", "square"), ("square", "hexagon")):
        workspace = _family_first_workspace(app, first)
        assert workspace["stage"] == "workspace"
        _, family = _request(app, "POST", "/api/designer/new", {})
        assert family["stage"] == "shape"
        assert family["selected_canvas_id"] is None
        assert family["selected_tile_shape"] is None
        assert family["selected_tile_id"] is None
        orientation = "straight" if second == "square" else "flat_top"
        _, sizes = _request(app, "POST", "/api/designer/shape", {
            "shape": second, "orientation": orientation,
        })
        assert sizes["stage"] == "tile"
        _, canvas = _request(app, "POST", "/api/designer/tile", {"tile_id": "s"})
        assert canvas["stage"] == "canvas" and canvas["project"] is None
        _, next_workspace = _request(app, "POST", "/api/designer/canvas", {
            "canvas_id": "landscape",
        })
        assert next_workspace["stage"] == "workspace"
        assert next_workspace["project"]["tile_family"] == second
        assert next_workspace["project"]["tile_orientation"] == orientation


def test_back_revises_same_mosaic_in_reverse_order_without_losing_selections():
    for family, orientation in (("hexagon", "point_top"), ("square", "straight")):
        app = MosaicDesignerApp()
        workspace = _family_first_workspace(app, family, "landscape")
        assert workspace["project"]["canvas_preset"]["id"] == "landscape"
        _, canvas = _request(app, "POST", "/api/designer/back", {})
        assert canvas["stage"] == "canvas"
        assert canvas["selected_canvas_id"] == "landscape"
        assert canvas["selected_tile_shape"] == family
        assert canvas["selected_tile_id"] == "m"
        _, tile = _request(app, "POST", "/api/designer/back", {})
        assert tile["stage"] == "tile"
        assert tile["selected_tile_shape"] == family
        assert tile["selected_tile_id"] == "m"
        assert tile["selected_tile_orientation"] == orientation
        _, shape = _request(app, "POST", "/api/designer/back", {})
        assert shape["stage"] == "shape"
        assert shape["selected_tile_shape"] == family


def test_hex_family_orientation_survives_size_canvas_designer_and_back():
    for orientation in ("point_top", "flat_top"):
        app = MosaicDesignerApp()
        _request(app, "POST", "/api/designer/new", {})
        _, sizes = _request(app, "POST", "/api/designer/shape", {
            "shape": "hexagon", "orientation": orientation,
        })
        assert sizes["stage"] == "tile"
        assert sizes["selected_tile_orientation"] == orientation
        _, canvas = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
        assert canvas["stage"] == "canvas"
        assert canvas["selected_tile_orientation"] == orientation
        _, workspace = _request(app, "POST", "/api/designer/canvas", {
            "canvas_id": "square",
        })
        assert workspace["project"]["tile_orientation"] == orientation
        _request(app, "POST", "/api/designer/back", {})
        _request(app, "POST", "/api/designer/back", {})
        _, family = _request(app, "POST", "/api/designer/back", {})
        assert family["stage"] == "shape"
        assert family["selected_tile_orientation"] == orientation


def test_square_normalizes_to_straight_and_new_mosaic_resets_orientation():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/new", {})
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "flat_top",
    })
    _, square = _request(app, "POST", "/api/designer/shape", {
        "shape": "square", "orientation": "straight",
    })
    assert square["selected_tile_orientation"] == "straight"
    _, reset = _request(app, "POST", "/api/designer/new", {})
    assert reset["stage"] == "shape"
    assert reset["selected_tile_shape"] is None
    assert reset["selected_tile_orientation"] is None
    status, error = _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "straight",
    })
    assert status == "400 Bad Request"
    assert "orientation" in error["error"].lower()


def test_new_mosaic_reset_is_authoritative_and_protects_dirty_work():
    app = MosaicDesignerApp()
    _family_first_workspace(app, "square")
    app.document_dirty = True
    app.document_title = "Prior Mosaic"
    status, conflict = _request(app, "POST", "/api/designer/new", {
        "canvas_first": False,
    })
    assert status == "409 Conflict" and conflict["requires_confirmation"] is True
    assert app.project is not None and app.document_title == "Prior Mosaic"

    _, reset = _request(app, "POST", "/api/designer/new", {
        "canvas_first": False, "discard_unsaved": True,
    })
    assert reset["stage"] == "shape"
    assert reset["project"] is None
    assert reset["selected_canvas_id"] is None
    assert reset["selected_tile_shape"] is None
    assert reset["selected_tile_id"] is None
    assert reset["selected_tile_orientation"] is None
    assert reset["document"] == {
        "title": "Untitled", "dirty": False, "has_file": False,
    }
    assert app.artwork is None and app.generated_artwork is None
    assert app.paint_overrides == {}


def test_open_mosaic_still_bypasses_setup(tmp_path):
    source = tmp_path / "square.mosaica"
    creator = MosaicDesignerApp(project_save_dialog=lambda _current: source)
    _family_first_workspace(creator, "square")
    _request(creator, "POST", "/api/designer/project/save", {})
    app = MosaicDesignerApp(project_open_dialog=lambda: source)
    _, opened = _request(app, "POST", "/api/designer/project/open", {})
    assert opened["stage"] == "workspace"
    assert opened["project"]["tile_family"] == "square"
    assert opened["document"]["dirty"] is False


def test_square_hover_uses_closed_visible_polygon_and_hex_keeps_parent_geometry():
    web = Path(__file__).parents[1] / "mosaica" / "web"
    script = (web / "designer.js").read_text()
    css = (web / "designer.css").read_text()
    assert 'tileHoverLayer.classList.add("tile-hover-outline-layer")' in script
    assert 'tileHoverLayer.appendChild(ghost)' in script
    assert script.index("svg.appendChild(boundary)") < script.index(
        "svg.appendChild(tileHoverLayer)"
    ) < script.index("svg.appendChild(tileHitLayer)")
    assert "return tile.full_vertices_in || tile.vertices_in;" in script
    assert "pointInPolygon(\n        physical.x, physical.y, tileInteractionVertices(tile)" in script
    assert ".tile-hover-outline-layer { pointer-events: none; }" in css
    assert ".partial-parent-ghost { fill: none" in css
    assert "refreshSquareHoverOutlines" not in script
    assert "0.75 / scale" not in script


def test_square_visible_hover_geometry_covers_every_edge_and_corners():
    shell = DesignerProjectShell.create_physical(
        "m", "straight", 4.0, 3.0, family_id="square",
    )
    payload = shell.to_dict()["geometry"]
    width, height = payload["width_in"], payload["height_in"]
    clipped = [tile for tile in payload["tiles"] if tile["piece_type"] == "edge_cut"]
    assert clipped

    def touches(tile, side):
        points = tile["vertices_in"]
        return any({
            "left": abs(x) < 1e-9,
            "right": abs(x - width) < 1e-9,
            "top": abs(y) < 1e-9,
            "bottom": abs(y - height) < 1e-9,
        }[side] for x, y in points)

    for side in ("left", "right", "top", "bottom"):
        assert any(touches(tile, side) for tile in clipped)
    assert any(
        sum(touches(tile, side) for side in ("left", "right", "top", "bottom")) >= 2
        for tile in clipped
    )
    assert all(len(tile["vertices_in"]) >= 4 for tile in clipped)
    assert all(tile["editable"] for tile in clipped)
    assert all(tile["vertices_in"] != tile["full_vertices_in"] for tile in clipped)


def _outside_parent_targets(geometry):
    width, height = geometry["width_in"], geometry["height_in"]
    targets = {}
    for tile in geometry["tiles"]:
        if tile["piece_type"] == "full":
            continue
        center_x, center_y = tile["parent_center_in"]
        for vertex_x, vertex_y in tile["full_vertices_in"]:
            point = (
                0.9 * vertex_x + 0.1 * center_x,
                0.9 * vertex_y + 0.1 * center_y,
            )
            x, y = point
            outside_x = x < 0 or x > width
            outside_y = y < 0 or y > height
            kind = "corner" if outside_x and outside_y else (
                "horizontal" if outside_x else "vertical" if outside_y else None
            )
            if (
                kind and kind not in targets
                and _point_in_polygon(x, y, tile["full_vertices_in"])
                and not _point_in_polygon(x, y, tile["vertices_in"])
            ):
                targets[kind] = (tile, point)
    return targets


def test_square_outside_canvas_parent_regions_resolve_and_paint_clipped_tiles():
    app = MosaicDesignerApp()
    app.project = DesignerProjectShell.create_physical(
        "m", "straight", 4.0, 3.0, family_id="square",
    )
    geometry = app.project.to_dict()["geometry"]
    targets = _outside_parent_targets(geometry)
    assert set(targets) == {"horizontal", "vertical", "corner"}

    for color_number, (tile, point) in enumerate(targets.values(), start=2):
        color_id = f"project-color-{color_number}"
        assert _point_in_polygon(*point, tile["full_vertices_in"])
        assert not _point_in_polygon(*point, tile["vertices_in"])
        status, painted = _request(app, "POST", "/api/designer/paint", {
            "placement_ids": [tile["id"]],
            "mode": "paint",
            "color_id": color_id,
        })
        assert status == "200 OK"
        assert app.paint_overrides[tile["id"]] == color_id
        update = next(value for value in painted["tile_updates"] if value["id"] == tile["id"])
        assert update["manual_override"] == color_id

    visible = app.project.to_dict(None, app.paint_overrides)["geometry"]
    assert all(
        0 <= x <= visible["width_in"] and 0 <= y <= visible["height_in"]
        for tile in visible["tiles"] for x, y in tile["vertices_in"]
    )


def test_hex_outside_canvas_parent_region_keeps_established_hit_and_paint_contract():
    app = MosaicDesignerApp()
    app.project = DesignerProjectShell.create_custom("m", "point_top", 10, 10)
    geometry = app.project.to_dict()["geometry"]
    targets = _outside_parent_targets(geometry)
    assert targets
    tile, point = next(iter(targets.values()))
    assert _point_in_polygon(*point, tile["full_vertices_in"])
    assert not _point_in_polygon(*point, tile["vertices_in"])
    status, _ = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile["id"]],
        "mode": "paint",
        "color_id": "project-color-3",
    })
    assert status == "200 OK"
    assert app.paint_overrides[tile["id"]] == "project-color-3"
    rendered = app.project.to_dict(None, app.paint_overrides)["geometry"]
    current = next(value for value in rendered["tiles"] if value["id"] == tile["id"])
    assert current["vertices_in"] == tile["vertices_in"]
    assert all(
        0 <= x <= rendered["width_in"] and 0 <= y <= rendered["height_in"]
        for x, y in current["vertices_in"]
    )


def test_family_cards_share_responsive_two_column_grid_and_accessible_controls():
    web = Path(__file__).parents[1] / "mosaica" / "web"
    html = (web / "designer.html").read_text()
    css = (web / "designer.css").read_text()
    assert '<div class="shape-presets">' in html
    assert 'id="shape-hexagon" class="choice-card shape-card"' in html
    assert 'id="shape-square" class="choice-card shape-card" type="button"' in html
    assert ".shape-presets { grid-template-columns: repeat(2, minmax(16rem, 22rem));" in css
    narrow = css[css.index("@media (max-width: 680px)"):]
    assert ".canvas-presets, .shape-presets, .tile-presets { grid-template-columns: 1fr; }" in narrow

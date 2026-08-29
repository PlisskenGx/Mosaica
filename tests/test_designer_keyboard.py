from io import BytesIO
from importlib.resources import files
import json

import pytest

from mosaica.designer import (
    DesignerProjectShell,
    MosaicDesignerApp,
    tile_keyboard_navigation,
)


def _request(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(app({
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": BytesIO(raw),
    }, start_response))
    return captured["status"], json.loads(response)


def _app(orientation="point_top"):
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": orientation,
    })
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": orientation,
    })
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    return app


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_keyboard_navigation_uses_physical_centers_for_both_orientations(orientation):
    geometry = DesignerProjectShell.create_custom("m", orientation, 5, 5).to_dict()["geometry"]
    navigation = geometry["keyboard_navigation"]
    tiles = {tile["id"]: tile for tile in geometry["tiles"]}
    assert geometry["keyboard_center_tile_id"] in tiles
    assert navigation == tile_keyboard_navigation(list(tiles.values()))[0]
    assert navigation == DesignerProjectShell.create_custom(
        "m", orientation, 5, 5,
    ).to_dict()["geometry"]["keyboard_navigation"]
    center_id = geometry["keyboard_center_tile_id"]
    right_id = navigation[center_id]["ArrowRight"]
    assert navigation[right_id]["ArrowLeft"] == center_id
    for tile_id, directions in navigation.items():
        x, y = tiles[tile_id]["center_in"]
        for key, neighbor_id in directions.items():
            nx, ny = tiles[neighbor_id]["center_in"]
            assert tiles[neighbor_id]["editable"] is True
            assert {
                "ArrowLeft": nx < x,
                "ArrowRight": nx > x,
                "ArrowUp": ny < y,
                "ArrowDown": ny > y,
            }[key]


def test_keyboard_navigation_includes_clipped_tiles_and_stops_at_edges():
    geometry = DesignerProjectShell.create_custom("m", "point_top", 4, 4).to_dict()["geometry"]
    tiles = {tile["id"]: tile for tile in geometry["tiles"]}
    clipped = next(tile for tile in tiles.values() if tile["piece_type"] != "full")
    assert clipped["id"] in geometry["keyboard_navigation"]
    assert all(
        neighbor in tiles
        for neighbor in geometry["keyboard_navigation"][clipped["id"]].values()
    )
    leftmost = min(tiles.values(), key=lambda tile: (tile["center_in"][0], tile["id"]))
    assert "ArrowLeft" not in geometry["keyboard_navigation"][leftmost["id"]]


def test_sparse_erase_restores_underlying_assignment_and_dirty_only_on_change():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    tile = initial["project"]["geometry"]["tiles"][0]
    _request(app, "POST", "/api/designer/paint", {
        "mode": "paint", "color_id": "project-color-2",
        "placement_ids": [tile["id"]],
    })
    app.document_dirty = False
    status, erased = _request(app, "POST", "/api/designer/paint/erase", {
        "placement_ids": [tile["id"]],
    })
    assert status == "200 OK"
    update = next(value for value in erased["tile_updates"] if value["id"] == tile["id"])
    assert update["manual_override"] is None
    assert update["color_id"] == update["lower_color_id"]
    assert erased["document"]["dirty"] is True

    app.document_dirty = False
    _, unchanged = _request(app, "POST", "/api/designer/paint/erase", {
        "placement_ids": [tile["id"]],
    })
    assert unchanged["document"]["dirty"] is False


def test_sparse_erase_is_atomic_for_unknown_placements():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    tile_id = initial["project"]["geometry"]["tiles"][0]["id"]
    _request(app, "POST", "/api/designer/paint", {
        "mode": "paint", "color_id": "project-color-2", "placement_ids": [tile_id],
    })
    app.document_dirty = False
    status, _ = _request(app, "POST", "/api/designer/paint/erase", {
        "placement_ids": [tile_id, "missing"],
    })
    assert status == "400 Bad Request"
    assert app.paint_overrides == {tile_id: "project-color-2"}
    assert app.document_dirty is False


def test_keyboard_workflow_is_centralized_and_uses_existing_paint_state():
    source = files("mosaica").joinpath("web/designer.js").read_text()
    markup = files("mosaica").joinpath("web/designer.html").read_text()
    assert "function designerShortcutAvailable(event)" in source
    assert "event.metaKey || event.ctrlKey || event.altKey" in source
    assert "input, textarea, select" in source
    assert "dialog[open], [role=dialog]:not([hidden])" in source
    assert '/^[1-2]$/.test(event.key)' in source
    assert "paint.curated_palette[Number(event.key) - 1]" in source
    assert 'event.key === " " && !event.repeat' in source
    assert "event.repeat" not in source.split(
        'if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]', 1
    )[1].split('if (/^[1-2]$/', 1)[0]
    assert "if (!color) return" in source
    assert 'event.shiftKey ? "erase" : paintTool' in source
    assert '"/api/designer/paint/erase"' in source
    assert "keyboard_navigation" in source
    assert "keyboard_center_tile_id" in source
    assert "activeGeometrySignature" in source
    assert 'state.stage !== "workspace"' in source
    assert "Artwork rotation remains deferred" in source
    assert "paint-shortcut-hint" not in markup


def test_keyboard_originated_edit_uses_authoritative_save_open_state(tmp_path):
    path = tmp_path / "Keyboard Edit.mosaica"
    app = MosaicDesignerApp(
        project_save_dialog=lambda _current: path,
        project_open_dialog=lambda: path,
    )
    app.project = DesignerProjectShell.create_custom("m", "point_top", 3, 3)
    app.tile_shape = "hexagon"
    app.tile_id = "m"
    app.tile_orientation = "point_top"
    app.canvas_id = "custom"
    tile_id = app.project.to_dict()["geometry"]["keyboard_center_tile_id"]
    _request(app, "POST", "/api/designer/paint", {
        "mode": "paint", "color_id": "project-color-2", "placement_ids": [tile_id],
    })
    _request(app, "POST", "/api/designer/project/save", {})
    app.paint_overrides = {}
    _request(app, "POST", "/api/designer/project/open", {
        "path": str(path), "discard_unsaved": True,
    })
    assert app.paint_overrides == {tile_id: "project-color-2"}

    _request(app, "POST", "/api/designer/paint/erase", {"placement_ids": [tile_id]})
    _request(app, "POST", "/api/designer/project/save", {})
    app.paint_overrides = {tile_id: "project-color-3"}
    _request(app, "POST", "/api/designer/project/open", {
        "path": str(path), "discard_unsaved": True,
    })
    assert app.paint_overrides == {}

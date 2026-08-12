from io import BytesIO
import json

import pytest

import mosaic_engine.designer_generation as generation_module
from mosaic_engine.designer import MosaicDesignerApp


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
    if captured["headers"]["Content-Type"].startswith("application/json"):
        response = json.loads(response)
    else:
        response = response.decode()
    return captured["status"], response


def _app():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    return app


def _full(payload):
    return next(tile for tile in payload["project"]["geometry"]["tiles"] if tile["editable"])


def _tile(payload, tile_id):
    return next(tile for tile in payload["project"]["geometry"]["tiles"] if tile["id"] == tile_id)


def test_batch_paint_uses_stable_ids_without_artwork_and_returns_compact_state():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    editable = [tile for tile in initial["project"]["geometry"]["tiles"] if tile["editable"]][:2]
    original_geometry = app.project.geometry

    status, painted = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [editable[0]["id"], editable[1]["id"], editable[0]["id"]],
        "mode": "paint",
        "color_id": "project-color-2",
    })

    assert status == "200 OK"
    assert painted["payload_kind"] == "design_state"
    assert "geometry" not in painted
    assert painted["paint"]["override_count"] == 2
    assert {tile["id"] for tile in painted["tile_updates"]} == {
        editable[0]["id"], editable[1]["id"],
    }
    assert app.project.geometry is original_geometry
    _, full = _request(app, "GET", "/api/designer")
    for tile in editable:
        current = _tile(full, tile["id"])
        assert current["manual_override"] == "project-color-2"
        assert current["color_id"] == "project-color-2"
        assert current["lower_color_id"] == "project-color-1"
    counts = {value["color_id"]: value["count"] for value in full["project"]["color_counts"]}
    assert counts["project-color-2"] == 2


def test_restore_and_clear_reveal_lower_assignment_and_mark_dirty_only_on_change():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    tile_id = _full(initial)["id"]
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "paint", "color_id": "project-color-3",
    })
    app.document_dirty = False

    _, restored = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "restore",
    })
    update = next(value for value in restored["tile_updates"] if value["id"] == tile_id)
    assert update["manual_override"] is None
    assert update["color_id"] == update["lower_color_id"] == "project-color-1"
    assert restored["document"]["dirty"] is True

    app.document_dirty = False
    _, cleared = _request(app, "POST", "/api/designer/paint/clear", {})
    assert cleared["paint"]["override_count"] == 0
    assert cleared["document"]["dirty"] is False


def test_protected_or_unknown_tiles_make_batch_paint_atomic():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    valid = _full(initial)["id"]
    protected = next(
        tile["id"] for tile in initial["project"]["geometry"]["tiles"]
        if tile["piece_type"] != "full"
    )

    status, error = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [valid, protected], "mode": "paint",
        "color_id": "project-color-2",
    })
    assert status == "400 Bad Request"
    assert "editable full placements" in error["error"]
    assert app.paint_overrides == {}
    assert app.document_dirty is False

    status, _ = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [valid, "missing"], "mode": "restore",
    })
    assert status == "400 Bad Request"
    assert app.paint_overrides == {}


def test_border_precedence_masks_paint_then_none_reveals_it():
    app = _app()
    _request(app, "POST", "/api/designer/border", {"preset_id": "solid"})
    _, solid = _request(app, "GET", "/api/designer")
    border_tile = next(
        tile for tile in solid["project"]["geometry"]["tiles"]
        if tile["border_owned"] and tile["piece_type"] == "full"
    )
    _request(app, "POST", "/api/designer/border", {"preset_id": "none"})
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [border_tile["id"]], "mode": "paint",
        "color_id": "project-color-3",
    })

    _request(app, "POST", "/api/designer/border", {"preset_id": "solid"})
    _, masked = _request(app, "GET", "/api/designer")
    tile = _tile(masked, border_tile["id"])
    assert tile["manual_override"] == "project-color-3"
    assert tile["color_id"] == "project-color-2"
    assert tile["editable"] is False

    _request(app, "POST", "/api/designer/border", {"preset_id": "none"})
    _, revealed = _request(app, "GET", "/api/designer")
    assert _tile(revealed, border_tile["id"])["color_id"] == "project-color-3"


def test_artwork_removal_retains_paint_but_geometry_rebuild_clears_it():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    tile_id = _full(initial)["id"]
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "paint", "color_id": "project-color-2",
    })
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10H0z"/></svg>'
    _request(app, "POST", "/api/designer/artwork/upload", {
        "filename": "mark.svg", "svg_content": svg,
    })
    _request(app, "POST", "/api/designer/artwork/remove", {})
    assert app.paint_overrides == {tile_id: "project-color-2"}

    _request(app, "POST", "/api/designer/tile", {"tile_id": "l"})
    assert app.paint_overrides == {}


def test_regeneration_and_artwork_transform_preserve_manual_paint(monkeypatch):
    monkeypatch.setattr(generation_module, "SVG_RASTER_WIDTH", 256)
    app = _app()
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10H0z" fill="#34373D"/></svg>'
    _request(app, "POST", "/api/designer/artwork/upload", {
        "filename": "mark.svg", "svg_content": svg,
    })
    _request(app, "POST", "/api/designer/artwork/generate", {})
    _, generated = _request(app, "GET", "/api/designer")
    tile = _full(generated)
    generated_color = tile["color_id"]
    override_color = (
        "project-color-1" if generated_color != "project-color-1"
        else "project-color-3"
    )
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile["id"]], "mode": "paint", "color_id": override_color,
    })
    generated_assignments = app.generated_artwork.assignments
    transform = app.artwork.transform
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": transform.x_in + 0.1,
    })
    assert app.paint_overrides[tile["id"]] == override_color
    _request(app, "POST", "/api/designer/artwork/generate", {})
    _, regenerated = _request(app, "GET", "/api/designer")
    current = _tile(regenerated, tile["id"])
    assert current["manual_override"] == override_color
    assert current["color_id"] == override_color
    assert app.generated_artwork.assignments is not generated_assignments


def test_paint_ui_uses_one_stroke_batch_with_rollback_and_no_geometry_math():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    assert 'id="paint-toggle"' not in html
    assert 'id="paint-mode-color"' not in html
    assert 'id="paint-mode-restore"' in html
    assert '>Erase</button>' in html
    assert 'id="paint-clear"' in html
    assert '>Clear Edits</button>' in html
    assert "window.confirm" not in script
    assert 'new Set()' in script
    assert '"/api/designer/paint"' in script
    assert "originalFills" in script
    assert "pointerup" in script
    assert "hex_geometry" not in script

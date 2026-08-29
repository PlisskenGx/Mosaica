from io import BytesIO
import json

import mosaica.designer_generation as generation_module
from mosaica.designer import MosaicDesignerApp


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


def test_toolbox_order_status_simplification_and_shared_chooser_contract():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    assert html.index('id="tiles-heading"') < html.index('id="grout-color"')
    assert html.index('id="grout-color"') < html.index('id="border-heading"')
    assert html.index('id="border-heading"') < html.index('id="artwork-heading"')
    assert 'id="grout-heading"' not in html
    assert "Build your mosaic" not in html and ">Design<" not in html
    assert 'id="colors-heading"' not in html
    assert "renderColorCounts" not in script
    assert ".physical-color-counts" not in css
    assert '`${geometry.visible_piece_count.toLocaleString()} pieces`' not in script
    assert script.count("function openDesignPalette") == 1
    assert "position: fixed" in css
    assert "paletteInvoker" in script
    assert 'performDesignerMutation("/api/designer/grout"' in script


def test_all_32_tile_inventory_remains_authoritative_but_counts_are_not_rendered():
    app = _app()
    _, payload = _request(app, "GET", "/api/designer")
    project = payload["project"]
    assert sum(value["count"] for value in project["color_counts"]) == (
        project["geometry"]["visible_piece_count"]
    )
    assert project["color_counts"] == [{
        "color_id": "project-color-1", "display_color": "#FAF9F6",
        "name": "Ivory", "count": project["geometry"]["visible_piece_count"],
        "order": 0,
    }]
    _, script = _request(app, "GET", "/designer.js")
    assert "counts.get(color.color_id) || 0" not in script
    assert 'countLabel.textContent = count.toLocaleString()' not in script
    assert 'swatch.setAttribute("aria-label", color.name)' in script


def test_grout_default_is_canonical_fixed_width_and_separate_state():
    app = _app()
    project = app.payload()["project"]
    assert project["grout"] == {
        "color_id": "project-color-1",
        "display_color": "#FAF9F6",
        "width_mm": 1.8,
    }
    assert project["grout_mm"] == 1.8
    assert project["color_system"]["role_to_color_id"]["background"] == (
        project["grout"]["color_id"]
    )
    assert "grout" in project and "grout" not in project["color_system"]["role_to_color_id"]


def test_grout_recolor_is_compact_and_does_not_change_tiles_or_counts():
    app = _app()
    before = app.payload()["project"]
    geometry = app.project.geometry
    overrides = dict(app.paint_overrides)
    status, changed = _request(app, "POST", "/api/designer/grout", {
        "color_id": "project-color-2",
    })
    assert status == "200 OK"
    assert "geometry" not in changed
    assert changed["tile_updates"] == []
    assert changed["grout"]["color_id"] == "project-color-2"
    assert changed["color_counts"] == before["color_counts"]
    assert changed["document"]["dirty"] is True
    assert app.project.geometry is geometry
    assert app.paint_overrides == overrides
    after = app.payload()["project"]
    assert after["geometry"]["tiles"] == before["geometry"]["tiles"]
    assert after["geometry"]["visible_piece_count"] == before["geometry"]["visible_piece_count"]


def test_clear_resets_all_tiles_toolbox_work_including_grout():
    app = _app()
    tile_id = app.payload()["project"]["geometry"]["tiles"][0]["id"]
    _request(app, "POST", "/api/designer/paint", {
        "mode": "paint", "color_id": "project-color-2",
        "placement_ids": [tile_id],
    })
    _request(app, "POST", "/api/designer/grout", {"color_id": "project-color-2"})
    status, cleared = _request(app, "POST", "/api/designer/paint/clear", {})
    assert status == "200 OK"
    assert cleared["paint"]["override_count"] == 0
    assert cleared["grout"]["color_id"] == "project-color-1"
    assert app.project.grout_color_id == "project-color-1"


def test_grout_is_independent_from_border_and_artwork(monkeypatch):
    monkeypatch.setattr(generation_module, "SVG_RASTER_WIDTH", 256)
    app = _app()
    _request(app, "POST", "/api/designer/border", {"preset_id": "solid"})
    _request(app, "POST", "/api/designer/artwork/upload", {
        "filename": "mark.svg",
        "svg_content": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#466984"/></svg>',
    })
    _request(app, "POST", "/api/designer/artwork/generate", {})
    generated = app.generated_artwork
    assignments = generated.assignments
    before = app.payload()["project"]
    status, _ = _request(app, "POST", "/api/designer/grout", {
        "color_id": "project-color-4",
    })
    assert status == "200 OK"
    assert app.generated_artwork is generated
    assert app.generated_artwork.assignments is assignments
    after = app.payload()["project"]
    assert after["border"]["channel_mappings"] == before["border"]["channel_mappings"]
    assert after["color_counts"] == before["color_counts"]


def test_svg_uses_a_panel_scoped_grout_field_not_workspace_background():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    assert 'groutField.classList.add("grout-field")' in script
    assert 'groutField.style.fill = project.grout.display_color' in script
    assert 'groutField.style.fill = state.project.grout.display_color' in script
    assert ".grout-field { pointer-events: none; }" in css
    assert "#mosaic-canvas" in css and "background: transparent" in css
    assert ".canvas-viewport" in css and "background: #e8e8eb" in css

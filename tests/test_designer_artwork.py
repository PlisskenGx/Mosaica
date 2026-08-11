from io import BytesIO
import json
from math import isclose

import pytest

from mosaic_engine.artwork import (
    INITIAL_FIT_FRACTION,
    available_artwork_bounds,
    create_artwork,
    sanitize_svg,
    update_artwork_transform,
)
from mosaic_engine.border import build_border_layer
from mosaic_engine.designer import DesignerProjectShell, MosaicDesignerApp


LANDSCAPE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <g fill="#1265a8" stroke="#111"><path d="M0 0L200 0L100 100Z"/><circle cx="100" cy="50" r="20"/></g>
</svg>"""
PORTRAIT_SVG = """<svg width="50mm" height="100mm"><rect width="50" height="100" fill="#c33"/></svg>"""


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


def _workspace(border="none"):
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    if border != "none":
        _request(app, "POST", "/api/designer/border", {"preset_id": border})
    return app


def _upload(app, filename="logo.svg", content=LANDSCAPE_SVG):
    return _request(app, "POST", "/api/designer/artwork/upload", {
        "filename": filename,
        "svg_content": content,
    })


def test_valid_svg_is_sanitized_as_vector_and_aspect_ratio_is_derived():
    safe, view_box = sanitize_svg("logo.svg", LANDSCAPE_SVG)
    assert view_box == (0.0, 0.0, 200.0, 100.0)
    assert "<path" in safe and "<circle" in safe
    assert "http://www.w3.org/2000/svg" in safe

    shell = DesignerProjectShell.create("square-s", "m")
    artwork = create_artwork(
        "folder/logo.svg", LANDSCAPE_SVG, shell.geometry,
        build_border_layer(shell.geometry, "none"),
    )
    assert artwork.source_filename == "logo.svg"
    assert artwork.source_aspect_ratio == 2.0
    assert artwork.selected is True


@pytest.mark.parametrize("filename,content,message", [
    ("logo.png", LANDSCAPE_SVG, "must be an SVG"),
    ("logo.svg", "<svg><path></svg>", "Malformed SVG"),
    ("logo.svg", "<html></html>", "SVG root"),
    ("logo.svg", '<svg viewBox="0 0 1 1"><script>alert(1)</script></svg>', "Unsafe SVG element"),
    ("logo.svg", '<svg viewBox="0 0 1 1"><path onload="alert(1)"/></svg>', "event handler"),
    ("logo.svg", '<svg viewBox="0 0 1 1"><use href="https://bad.example/x.svg#x"/></svg>', "external resource"),
    ("logo.svg", '<svg viewBox="0 0 1 1"><foreignObject/></svg>', "Unsafe SVG element"),
])
def test_invalid_or_unsafe_svg_is_rejected(filename, content, message):
    with pytest.raises(ValueError, match=message):
        sanitize_svg(filename, content)


def test_internal_svg_references_and_absolute_source_dimensions_are_supported():
    safe, view_box = sanitize_svg(
        "portrait.svg",
        '<svg width="50mm" height="100mm"><defs><path id="p" d="M0 0L1 1"/></defs><use href="#p"/></svg>',
    )
    assert '<use href="#p"' in safe
    assert isclose(view_box[2] / view_box[3], 0.5)


def test_initial_transform_is_deterministic_centered_and_proportional():
    shell = DesignerProjectShell.create("square-s", "m")
    border = build_border_layer(shell.geometry, "solid")
    first = create_artwork("logo.svg", LANDSCAPE_SVG, shell.geometry, border)
    second = create_artwork("logo.svg", LANDSCAPE_SVG, shell.geometry, border)
    left, top, right, bottom = available_artwork_bounds(shell.geometry, border)
    transform = first.transform
    assert first == second
    assert isclose(transform.width_in / transform.height_in, 2.0)
    assert isclose(transform.x_in + transform.width_in / 2, (left + right) / 2)
    assert isclose(transform.y_in + transform.height_in / 2, (top + bottom) / 2)
    assert transform.width_in <= (right - left) * INITIAL_FIT_FRACTION + 1e-9
    assert transform.height_in <= (bottom - top) * INITIAL_FIT_FRACTION + 1e-9


def test_artwork_api_uses_physical_state_and_artwork_only_transform_response():
    app = _workspace("solid")
    project_before = app.project
    geometry_before = app.project.geometry
    design_before = app.project.to_dict()
    status, uploaded = _upload(app)
    assert status == "200 OK"
    assert uploaded["payload_kind"] == "artwork_state"
    assert uploaded["generated_artwork"] is None
    transform = uploaded["artwork"]["transform"]
    assert set(transform) == {"x_in", "y_in", "width_in", "height_in"}

    moved = {**transform, "x_in": transform["x_in"] + 1.25, "y_in": -0.75}
    status, response = _request(
        app, "POST", "/api/designer/artwork/transform", moved,
    )
    assert status == "200 OK"
    assert response["artwork"]["transform"] == moved
    assert response["artwork"]["transform"]["width_in"] == transform["width_in"]
    assert response["artwork"]["transform"]["height_in"] == transform["height_in"]
    assert app.project is project_before
    assert app.project.geometry is geometry_before
    assert app.project.to_dict() == design_before


def test_scale_is_proportional_can_exceed_canvas_and_rejects_invalid_sizes():
    app = _workspace()
    _, uploaded = _upload(app)
    current = uploaded["artwork"]["transform"]
    oversized = {"x_in": -20.0, "y_in": -10.0, "width_in": 60.0, "height_in": 30.0}
    status, response = _request(
        app, "POST", "/api/designer/artwork/transform", oversized,
    )
    assert status == "200 OK"
    assert response["artwork"]["transform"] == oversized

    for width, height in ((0, 0), (-1, -0.5), (5, 5)):
        status, _ = _request(app, "POST", "/api/designer/artwork/transform", {
            **current, "width_in": width, "height_in": height,
        })
        assert status == "400 Bad Request"
    assert app.artwork.transform.width_in == 60.0


def test_selection_changes_only_session_selection_state():
    app = _workspace()
    _upload(app)
    transform = app.artwork.transform
    geometry = app.project.geometry
    border = app.project.border_preset_id
    status, response = _request(
        app, "POST", "/api/designer/artwork/selection", {"selected": False},
    )
    assert status == "200 OK"
    assert response["artwork"]["selected"] is False
    assert app.artwork.transform == transform
    assert app.project.geometry is geometry
    assert app.project.border_preset_id == border


def test_replace_remove_and_reset_are_artwork_only_actions():
    app = _workspace("solid")
    baseline = app.project.to_dict()
    _upload(app)
    original_svg = app.artwork.sanitized_svg
    original_transform = app.artwork.transform
    moved = {
        "x_in": -2.0, "y_in": 3.0,
        "width_in": original_transform.width_in * 1.5,
        "height_in": original_transform.height_in * 1.5,
    }
    _request(app, "POST", "/api/designer/artwork/transform", moved)
    _, reset = _request(app, "POST", "/api/designer/artwork/reset", {})
    assert reset["artwork"]["transform"] == original_transform.to_dict()
    assert reset["artwork"]["sanitized_svg"] == original_svg

    _, replaced = _request(app, "POST", "/api/designer/artwork/replace", {
        "filename": "portrait.svg", "svg_content": PORTRAIT_SVG,
    })
    replacement = replaced["artwork"]
    assert replacement["source_filename"] == "portrait.svg"
    assert replacement["source_aspect_ratio"] == pytest.approx(0.5)
    assert replacement["sanitized_svg"] != original_svg
    assert replacement["transform"]["width_in"] / replacement["transform"]["height_in"] == pytest.approx(0.5)

    status, removed = _request(app, "POST", "/api/designer/artwork/remove", {})
    assert status == "200 OK" and removed["artwork"] is None
    assert app.project.to_dict() == baseline


def test_border_change_preserves_transform_and_reset_uses_current_field():
    app = _workspace()
    _, uploaded = _upload(app)
    original = uploaded["artwork"]["transform"]
    moved = {**original, "x_in": -3.0, "y_in": 4.0}
    _request(app, "POST", "/api/designer/artwork/transform", moved)
    _, bordered = _request(
        app, "POST", "/api/designer/border", {"preset_id": "double"},
    )
    assert bordered["artwork"]["transform"] == moved
    assert bordered["border"]["preset_id"] == "double"

    _, reset = _request(app, "POST", "/api/designer/artwork/reset", {})
    expected = create_artwork(
        "logo.svg", LANDSCAPE_SVG, app.project.geometry,
        build_border_layer(app.project.geometry, "double"),
    ).transform.to_dict()
    assert reset["artwork"]["transform"] == expected


def test_geometry_setup_navigation_clears_session_artwork():
    app = _workspace()
    _upload(app)
    _, payload = _request(app, "POST", "/api/designer/back", {})
    assert payload["stage"] == "tile"
    assert app.artwork is None
    _, payload = _request(app, "POST", "/api/designer/tile", {"tile_id": "l"})
    assert payload["project"]["artwork"] is None


def test_artwork_does_not_change_tile_state_or_physical_color_counts():
    app = _workspace("alternating")
    before = app.payload()["project"]
    _upload(app)
    transform = app.artwork.transform
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": transform.x_in + 2,
        "y_in": transform.y_in + 1,
        "width_in": transform.width_in,
        "height_in": transform.height_in,
    })
    after = app.payload()["project"]
    assert after["geometry"] == before["geometry"]
    assert after["border"] == before["border"]
    assert after["color_counts"] == before["color_counts"]
    assert "generated_grid" not in after


def test_frontend_uses_vector_pointer_interaction_and_four_corner_handles():
    app = MosaicDesignerApp()
    _, html = _asset_request(app, "/")
    assert 'accept=".svg,image/svg+xml"' in html
    for action in ("Upload SVG", "Replace", "Remove", "Reset"):
        assert action in html
    _, script = _asset_request(app, "/designer.js")
    assert "DOMParser" in script
    assert "artwork.sanitized_svg" in script
    assert 'addEventListener("pointerdown"' in script
    assert 'addEventListener("pointermove"' in script
    assert 'addEventListener("pointerup"' in script
    assert "setPointerCapture" in script
    assert "scaledArtworkTransform" in script
    assert '["nw"' in script and '["ne"' in script and '["se"' in script and '["sw"' in script
    assert "rotation" not in script.lower()
    assert '"/api/designer/artwork/transform"' in script
    assert '"/api/designer/artwork/replace"' in script
    assert "clientX" in script and "matrixTransform" in script
    _, css = _asset_request(app, "/designer.css")
    assert ".artwork-selection" in css
    assert ".artwork-handle-target" in css
    assert "touch-action: none" in css


def _asset_request(app, path):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(app({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(),
    }, start_response)).decode()
    return captured["status"], response


def test_direct_transform_model_is_viewport_independent():
    shell = DesignerProjectShell.create("landscape", "s")
    border = build_border_layer(shell.geometry, "none")
    artwork = create_artwork("logo.svg", LANDSCAPE_SVG, shell.geometry, border)
    before = artwork.transform
    # Viewport dimensions are intentionally absent from all physical APIs.
    moved = update_artwork_transform(
        artwork,
        x_in=before.x_in + 1,
        y_in=before.y_in + 2,
        width_in=before.width_in,
        height_in=before.height_in,
    )
    assert moved.transform.width_in == before.width_in
    assert moved.transform.height_in == before.height_in
    assert shell.geometry.width_in == 48.0

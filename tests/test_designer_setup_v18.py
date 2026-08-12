from io import BytesIO
import json
from math import isclose

import pytest

from mosaic_engine.designer import (
    CANVAS_PRESETS, CUSTOM_GRID_MAX, DesignerProjectShell, MosaicDesignerApp,
)


def _request(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    captured = {}
    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)
    response = b"".join(app({
        "REQUEST_METHOD": method, "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw),
    }, start_response))
    if captured["headers"]["Content-Type"].startswith("application/json"):
        response = json.loads(response)
    else:
        response = response.decode()
    return captured["status"], response


def _configured_app(orientation="point_top"):
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {"shape": "hexagon"})
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": orientation,
    })
    return app


def test_canonical_setup_flow_and_back_navigation():
    app = MosaicDesignerApp()
    _, initial = _request(app, "GET", "/api/designer")
    assert initial["stage"] == "shape"
    _, tile = _request(app, "POST", "/api/designer/shape", {"shape": "hexagon"})
    assert tile["stage"] == "tile"
    assert tile["selected_tile_shape"] == "hexagon"
    _, canvas = _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": "flat_top",
    })
    assert canvas["stage"] == "canvas"
    assert all("actual" in value for value in canvas["canvas_presets"])
    _, workspace = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "square-s",
    })
    assert workspace["stage"] == "workspace"
    _request(app, "POST", "/api/designer/back", {})
    assert _request(app, "GET", "/api/designer")[1]["stage"] == "canvas"
    _request(app, "POST", "/api/designer/back", {})
    assert _request(app, "GET", "/api/designer")[1]["stage"] == "tile"
    _request(app, "POST", "/api/designer/back", {})
    assert _request(app, "GET", "/api/designer")[1]["stage"] == "shape"


def test_setup_back_is_local_and_accepts_the_shape_setup_payload():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    assert '["shape", "canvas", "tile", "workspace"]' in script

    back_handler = script[
        script.index('byId("back").addEventListener'):
        script.index('const shapePreview')
    ]
    assert 'state.stage === "tile"' in back_handler
    assert 'state = { ...state, stage: "shape" }' in back_handler
    assert 'state.stage === "canvas"' in back_handler
    assert 'state = { ...state, stage: "tile" }' in back_handler
    assert back_handler.count('/api/designer/back') == 1
    assert 'state.stage === "workspace"' in back_handler


def test_setup_back_preserves_selections_without_creating_geometry():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "flat_top",
    })
    _, canvas = _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": "flat_top",
    })

    # Setup-only Back is a frontend stage transition, so canonical setup
    # selections remain available and no project geometry is constructed.
    assert canvas["selected_tile_shape"] == "hexagon"
    assert canvas["selected_tile_orientation"] == "flat_top"
    assert canvas["selected_tile_id"] == "m"
    assert canvas["project"] is None


def test_six_ui_canvas_choices_replace_panoramic_with_custom():
    assert [value.name for value in CANVAS_PRESETS] == [
        "Small Square", "Medium Square", "Large Square", "Landscape", "Wide",
    ]
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    assert "Panoramic" not in html + script
    assert '{ id: "custom", name: "Custom"' in script
    assert 'id="custom-across"' in script
    assert 'id="custom-down"' in script
    assert 'class="custom-lattice ${state.selected_tile_orientation}"' in script


def test_orientation_belongs_to_shape_and_tile_cards_are_size_only():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    assert html.index("Hexagon") < html.index('data-shape-orientation="flat_top"')
    assert 'data-shape-orientation="point_top" aria-pressed="true"' in html
    tile_render = script[
        script.index("function renderTilePresets"):
        script.index("function renderWorkspace")
    ]
    assert "orientation-choices" not in tile_render
    assert "data-orientation" not in tile_render
    assert "state.selected_tile_orientation" in script


def test_custom_configuration_is_in_card_and_schematic_is_bounded():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    assert 'id="custom-canvas"' not in html
    assert "customCanvasActive ?" in script
    assert "custom-fields" in script
    assert script.count('class="lattice-hex') == 5
    assert "h1" in script and "h5" in script
    assert ".custom-lattice.flat_top" in css
    assert ".measure.across" in css and ".measure.down" in css
    assert "height: calc(100dvh - var(--app-bar-height))" in css
    assert 'byId("setup-viewport").dataset.stage = stage' in script


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
@pytest.mark.parametrize("across,down", ((1, 1), (7, 4), (8, 5), (40, 24), (80, 3)))
def test_custom_grid_is_deterministic_vertex_constrained(orientation, across, down):
    first = DesignerProjectShell.create_custom("m", orientation, across, down)
    second = DesignerProjectShell.create_custom("m", orientation, across, down)
    assert first.geometry == second.geometry
    assert first.canvas_mode == "custom_grid"
    assert first.tiles_across == across and first.tiles_down == down
    for placement in first.geometry.placements:
        if placement.piece_type not in {"full", "outside"}:
            assert any(isclose(placement.piece_fraction, value, abs_tol=1e-8) for value in (1/6, 1/2))
            assert all(any(isclose(point[0], vertex[0], abs_tol=1e-8) and isclose(point[1], vertex[1], abs_tol=1e-8) for vertex in placement.full_vertices_in) for point in placement.vertices_in)


@pytest.mark.parametrize("value", (0, -1, 1.5, "x", CUSTOM_GRID_MAX + 1))
def test_custom_grid_validation(value):
    with pytest.raises(ValueError):
        DesignerProjectShell.create_custom("m", "point_top", value, 10)


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
def test_custom_preview_exactly_matches_created_workspace(orientation):
    app = _configured_app(orientation)
    body = {"canvas_id": "custom", "tiles_across": 17, "tiles_down": 9}
    status, preview = _request(app, "POST", "/api/designer/canvas-preview", body)
    assert status == "200 OK"
    status, workspace = _request(app, "POST", "/api/designer/canvas", body)
    assert status == "200 OK"
    geometry = workspace["project"]["geometry"]
    assert preview["width_in"] == geometry["width_in"]
    assert preview["height_in"] == geometry["height_in"]
    assert workspace["project"]["custom_grid"] == {
        "tiles_across": 17, "tiles_down": 9,
    }

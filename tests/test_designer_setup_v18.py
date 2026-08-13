from io import BytesIO
import json
from math import isclose

import pytest

from mosaic_engine.designer import (
    CANVAS_PRESETS, CUSTOM_GRID_MAX, TILE_PRESETS,
    DesignerProjectShell, MosaicDesignerApp,
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
        "canvas_id": "square",
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
    assert 'document.querySelectorAll(".setup-back")' in back_handler
    assert 'stage: button.dataset.backStage' in back_handler
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


def test_three_primary_canvas_choices_and_dedicated_custom_action():
    assert [value.name for value in CANVAS_PRESETS] == [
        "Square", "Portrait", "Landscape",
    ]
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    assert "Panoramic" not in html + script
    assert 'id="custom-size"' in html
    assert 'id="custom-screen"' in html
    assert 'id="custom-across"' in html
    assert 'id="custom-down"' in html
    assert '`custom-lattice ${state.selected_tile_orientation}`' in script
    assert "Small Square" not in script
    assert "Medium Square" not in script
    assert "Large Square" not in script
    assert "Wide" not in script
    assert "choice-actual" not in script


def test_setup_copy_orientation_previews_and_neutral_controls():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    assert "Six-sided tile · versatile geometric layouts" in html
    assert 'class="hex-preview ${state.selected_tile_orientation}"' in script
    assert 'state.selected_tile_orientation === "flat_top"' in script
    assert "No hardcoded Point Top preview" not in script
    assert '.orientation-choices button[aria-pressed="true"]' not in css
    assert 'choice.setAttribute("aria-pressed", String(choice === button))' in script
    assert ".orientation-choices button:focus-visible" in css


def test_custom_stage_copy_controls_and_paint_action_row():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, css = _request(app, "GET", "/designer.css")
    assert "Create custom canvas" in html
    assert "Tiles Across" in html and "Tiles Down" in html
    assert "Counts refer to the full-tile grid. Edge pieces are added automatically." in html
    assert "Finished size" in html and "Create Canvas" in html
    paint_actions = html[html.index('class="paint-actions"'):html.index("</div>", html.index('class="paint-actions"'))]
    assert "paint-mode-restore" in paint_actions
    assert "paint-clear" in paint_actions
    assert ".paint-actions { display: flex; align-items: center" in css


def test_local_setup_back_controls_and_custom_teaching_lattice():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    shape = html[html.index('id="shape-screen"'):html.index('id="canvas-screen"')]
    tile = html[html.index('id="tile-screen"'):html.index('id="custom-screen"')]
    canvas = html[html.index('id="canvas-screen"'):html.index('id="tile-screen"')]
    custom = html[html.index('id="custom-screen"'):html.index('id="workspace"')]
    assert "setup-back" not in shape
    assert 'data-back-stage="shape"' in tile
    assert 'data-back-stage="tile"' in canvas
    assert 'data-back-stage="canvas"' in custom
    assert 'byId("back").hidden = stage !== "workspace"' in script
    assert "Tiles Across = 5" in custom
    assert "Tiles Down = 3" in custom
    assert custom.count('class="lattice-hex') == 15
    assert 'custom-lattice ${state.selected_tile_orientation}' in script
    assert ".custom-lattice.point_top .lattice-hex" in css
    assert ".custom-lattice.flat_top .lattice-hex" in css
    assert ".setup-back:focus-visible" in css


def test_canvas_cards_use_common_larger_illustration_region():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    assert 'style="--aspect:${preset.aspect_ratio}"' in script
    assert ".canvas-card { min-height: 17rem; }" in css
    assert ".canvas-preview-wrap { display: grid; height: 10.5rem" in css
    assert "height: min(8rem, 82%)" in css


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


def test_tile_character_terminology_is_consistent():
    assert [(value.id, value.title) for value in TILE_PRESETS] == [
        ("s", "Detailed"), ("m", "Balanced"), ("l", "Bold"),
    ]


def test_custom_configuration_has_dedicated_stage_and_bounded_schematic():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, css = _request(app, "GET", "/designer.css")
    assert 'id="custom-canvas"' not in html
    assert 'state = { ...state, stage: "custom" }' in script
    assert 'data-back-stage="canvas"' in html
    assert "custom-fields" in html
    assert html.count('class="lattice-hex') == 15
    assert "c0 r0" in html and "c4 r2" in html
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

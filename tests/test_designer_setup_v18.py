from io import BytesIO
import json
from math import isclose

import pytest

from mosaic_engine.boundary import clip_polygon_to_rect, polygon_area
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


def test_changing_tile_after_workspace_back_requires_canvas_confirmation():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "point_top",
    })
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "l", "orientation": "point_top",
    })
    _, workspace = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "landscape",
    })
    assert workspace["stage"] == "workspace"

    _, canvas = _request(app, "POST", "/api/designer/back", {})
    assert canvas["stage"] == "canvas"
    assert canvas["selected_canvas_id"] == "landscape"

    # Canvas -> Tile is a local setup transition in the browser. Selecting a
    # different tile through the canonical API must not reuse the remembered
    # canvas as implicit permission to reopen Workspace.
    _, changed = _request(app, "POST", "/api/designer/tile", {
        "tile_id": "s", "orientation": "point_top",
    })
    assert changed["stage"] == "canvas"
    assert changed["project"] is None
    assert changed["selected_canvas_id"] == "landscape"

    _, final = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "square",
    })
    assert final["stage"] == "workspace"
    assert final["project"]["tile_preset"]["id"] == "s"
    assert final["project"]["canvas_preset"]["id"] == "square"


def test_orientation_change_resumes_ordered_setup_before_workspace():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "point_top",
    })
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "l", "orientation": "point_top",
    })
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "landscape"})
    _request(app, "POST", "/api/designer/back", {})

    _, tile = _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "flat_top",
    })
    assert tile["stage"] == "tile"
    _, canvas = _request(app, "POST", "/api/designer/tile", {
        "tile_id": "l", "orientation": "flat_top",
    })
    assert canvas["stage"] == "canvas"
    assert canvas["project"] is None
    _, workspace = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "landscape",
    })
    assert workspace["stage"] == "workspace"
    assert workspace["project"]["tile_orientation"] == "flat_top"


def test_changing_tile_before_recreating_custom_canvas_does_not_open_workspace():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "point_top",
    })
    _request(app, "POST", "/api/designer/tile", {"tile_id": "l"})
    _, original = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "custom", "tiles_across": 10, "tiles_down": 10,
    })
    assert original["stage"] == "workspace"
    _request(app, "POST", "/api/designer/back", {})

    _, canvas = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    assert canvas["stage"] == "canvas"
    assert canvas["project"] is None
    assert canvas["selected_canvas_id"] == "custom"

    _, recreated = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "custom", "tiles_across": 10, "tiles_down": 10,
    })
    assert recreated["stage"] == "workspace"
    assert recreated["project"]["tile_preset"]["id"] == "m"
    assert recreated["project"]["custom_grid"] == {
        "tiles_across": 10, "tiles_down": 10,
    }


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
@pytest.mark.parametrize("across,down", (
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (5, 3), (3, 5),
    (6, 6), (10, 10), (10, 5), (5, 10), (40, 24),
))
def test_custom_grid_is_deterministic_vertex_constrained(orientation, across, down):
    first = DesignerProjectShell.create_custom("m", orientation, across, down)
    second = DesignerProjectShell.create_custom("m", orientation, across, down)
    assert first.geometry == second.geometry
    assert first.canvas_mode == "custom_grid"
    assert first.tiles_across == across and first.tiles_down == down
    principal = [
        placement for placement in first.geometry.placements
        if placement.principal_grid
    ]
    assert len(principal) == across * down
    assert all(placement.piece_type == "full" for placement in principal)
    assert all(placement.vertices_in == placement.full_vertices_in for placement in principal)
    assert {
        (placement.principal_row, placement.principal_column)
        for placement in principal
    } == {
        (row, column)
        for row in range(down) for column in range(across)
    }
    assert all(
        0 <= point[0] <= first.geometry.width_in
        and 0 <= point[1] <= first.geometry.height_in
        for placement in principal for point in placement.vertices_in
    )
    assert any(
        placement.piece_type not in {"full", "outside"}
        for placement in first.geometry.placements
    )
    for placement in first.geometry.placements:
        if placement.piece_type not in {"full", "outside"}:
            assert placement.piece_fraction >= 1 / 6 - 1e-8
            even_stagger = (
                down % 2 == 0 if orientation == "point_top"
                else across % 2 == 0
            )
            if not even_stagger:
                assert any(isclose(placement.piece_fraction, value, abs_tol=1e-8) for value in (1/6, 1/2))
                assert all(any(isclose(point[0], vertex[0], abs_tol=1e-8) and isclose(point[1], vertex[1], abs_tol=1e-8) for vertex in placement.full_vertices_in) for point in placement.vertices_in)


@pytest.mark.parametrize("across", (3, 4, 5, 6, 9, 10))
@pytest.mark.parametrize("down", (3, 4, 5, 6, 9, 10))
def test_point_top_custom_principal_rows_are_exact_for_every_parity(across, down):
    geometry = DesignerProjectShell.create_custom(
        "l", "point_top", across, down,
    ).geometry
    principal = [value for value in geometry.placements if value.principal_grid]
    rows = {
        row: [value for value in principal if value.principal_row == row]
        for row in range(down)
    }
    assert len(principal) == across * down
    assert set(value.principal_row for value in principal) == set(range(down))
    assert set(value.principal_column for value in principal) == set(range(across))
    assert all(len(values) == across for values in rows.values())
    assert all(value.piece_type == "full" for value in principal)


@pytest.mark.parametrize("across", (3, 4, 5, 6, 9, 10))
@pytest.mark.parametrize("down", (3, 4, 5, 6, 9, 10))
def test_flat_top_custom_principal_columns_are_exact_for_every_parity(across, down):
    geometry = DesignerProjectShell.create_custom(
        "l", "flat_top", across, down,
    ).geometry
    principal = [value for value in geometry.placements if value.principal_grid]
    columns = {
        column: [
            value for value in principal if value.principal_column == column
        ]
        for column in range(across)
    }
    assert len(principal) == across * down
    assert set(value.principal_row for value in principal) == set(range(down))
    assert set(value.principal_column for value in principal) == set(range(across))
    assert all(len(values) == down for values in columns.values())
    assert all(value.piece_type == "full" for value in principal)


@pytest.mark.parametrize("orientation,across,down", (
    ("point_top", 5, 4), ("point_top", 5, 6),
    ("point_top", 10, 10), ("flat_top", 4, 5),
    ("flat_top", 6, 5), ("flat_top", 10, 10),
))
def test_even_stagger_mid_side_phase_avoids_extra_supplemental_full_strip(
    orientation, across, down,
):
    geometry = DesignerProjectShell.create_custom(
        "l", orientation, across, down,
    ).geometry
    supplemental_full = [
        value for value in geometry.placements
        if value.piece_type == "full" and not value.principal_grid
    ]
    assert supplemental_full == []


def test_flat_top_five_by_five_preserves_bottom_principal_positions():
    shell = DesignerProjectShell.create_custom("l", "flat_top", 5, 5)
    payload = shell.to_dict()
    principal = {
        tile["id"]: tile for tile in payload["geometry"]["tiles"]
        if tile["principal_grid"]
    }
    assert len(principal) == 25
    assert all(tile["piece_type"] == "full" for tile in principal.values())
    assert {
        (tile["principal_row"], tile["principal_column"])
        for tile in principal.values()
    } == {(row, column) for row in range(5) for column in range(5)}


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
@pytest.mark.parametrize("across,down", (
    (1, 1), (2, 2), (3, 3), (5, 3), (3, 5), (5, 5),
    (10, 10), (10, 5), (5, 10), (40, 24),
))
def test_custom_grid_retains_every_positive_area_lattice_intersection(
    orientation, across, down,
):
    geometry = DesignerProjectShell.create_custom(
        "l", orientation, across, down,
    ).geometry
    panel = geometry.panel_bounds
    intersecting = []
    for placement in geometry.placements:
        clipped = clip_polygon_to_rect(placement.full_vertices_in, panel)
        if polygon_area(clipped) > 1e-10:
            intersecting.append(placement)
            assert placement.piece_type != "outside"
            assert placement.vertices_in
        else:
            assert placement.piece_type == "outside"

    assert len({(value.row, value.column) for value in intersecting}) == len(intersecting)
    supplemental = [value for value in intersecting if not value.principal_grid]
    assert all(value.piece_fraction >= 1 / 6 - 1e-8 for value in supplemental)


@pytest.mark.parametrize("orientation,across,down", (
    ("point_top", 1, 1), ("point_top", 2, 2),
    ("point_top", 3, 3), ("point_top", 4, 4),
    ("point_top", 5, 3), ("point_top", 3, 5),
    ("point_top", 5, 5), ("point_top", 6, 6),
    ("point_top", 10, 10), ("point_top", 10, 5),
    ("point_top", 5, 10), ("point_top", 40, 24),
    ("flat_top", 1, 1), ("flat_top", 2, 2),
    ("flat_top", 3, 3), ("flat_top", 4, 4),
    ("flat_top", 5, 3), ("flat_top", 3, 5),
    ("flat_top", 5, 5), ("flat_top", 6, 6),
    ("flat_top", 10, 10), ("flat_top", 10, 5),
    ("flat_top", 5, 10), ("flat_top", 40, 24),
))
def test_custom_visible_stagger_count_and_field_center_are_exact(
    orientation, across, down,
):
    geometry = DesignerProjectShell.create_custom(
        "m", orientation, across, down,
    ).geometry
    full = [value for value in geometry.placements if value.piece_type == "full"]
    axis = "center_y_in" if orientation == "point_top" else "center_x_in"
    expected = down if orientation == "point_top" else across
    assert len({round(getattr(value, axis), 10) for value in full}) == expected

    principal = [value for value in full if value.principal_grid]
    xs = [point[0] for value in principal for point in value.vertices_in]
    ys = [point[1] for value in principal for point in value.vertices_in]
    even_stagger = down % 2 == 0 if orientation == "point_top" else across % 2 == 0
    if even_stagger:
        assert isclose((min(xs) + max(xs)) / 2, geometry.width_in / 2, abs_tol=1e-9)
        assert isclose((min(ys) + max(ys)) / 2, geometry.height_in / 2, abs_tol=1e-9)
    assert all(
        value.piece_fraction >= 1 / 6 - 1e-8
        for value in geometry.placements if value.piece_type != "outside"
    )


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
def test_principal_payload_preserves_logical_coordinates_and_rigid_centers(orientation):
    shell = DesignerProjectShell.create_custom("l", orientation, 5, 5)
    payload = shell.to_dict()
    principal = [
        tile for tile in payload["geometry"]["tiles"]
        if tile["principal_grid"]
    ]
    assert {
        (tile["principal_row"], tile["principal_column"])
        for tile in principal
    } == {(row, column) for row in range(5) for column in range(5)}
    assert all(tile["center_in"] for tile in principal)
    assert all(tile["full_vertices_in"] == tile["vertices_in"] for tile in principal)
    assert all(
        tile["principal_row"] is None and tile["principal_column"] is None
        for tile in payload["geometry"]["tiles"]
        if not tile["principal_grid"]
    )

    by_key = {
        (tile["principal_row"], tile["principal_column"]): tile["center_in"]
        for tile in principal
    }
    if orientation == "flat_top":
        # Alternate columns share one rigid half-pitch vertical offset.
        assert by_key[(0, 1)][1] > by_key[(0, 0)][1]
        assert isclose(
            by_key[(0, 1)][1] - by_key[(0, 0)][1],
            by_key[(0, 3)][1] - by_key[(0, 2)][1],
        )
    else:
        # Alternate rows share one rigid half-pitch horizontal offset.
        assert by_key[(1, 0)][0] > by_key[(0, 0)][0]
        assert isclose(
            by_key[(1, 0)][0] - by_key[(0, 0)][0],
            by_key[(3, 0)][0] - by_key[(2, 0)][0],
        )


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
def test_ten_by_ten_principal_union_is_contained_serialized_and_renderable(orientation):
    shell = DesignerProjectShell.create_custom("l", orientation, 10, 10)
    payload = shell.to_dict()
    geometry = payload["geometry"]
    principal = [tile for tile in geometry["tiles"] if tile["principal_grid"]]
    assert len(principal) == 100
    assert all(tile["piece_type"] == "full" for tile in principal)
    assert all(tile["vertices_in"] == tile["full_vertices_in"] for tile in principal)
    assert all(
        0 <= x <= geometry["width_in"] and 0 <= y <= geometry["height_in"]
        for tile in principal for x, y in tile["vertices_in"]
    )
    assert len({tile["id"] for tile in principal}) == 100

    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    assert 'polygon.classList.add("principal-grid")' in script
    assert "polygon.dataset.principalRow = tile.principal_row" in script
    assert "polygon.dataset.principalColumn = tile.principal_column" in script


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

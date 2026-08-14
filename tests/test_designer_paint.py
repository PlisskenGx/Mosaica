from io import BytesIO
import json

import pytest

import mosaic_engine.designer_generation as generation_module
from mosaic_engine.designer import DesignerProjectShell, MosaicDesignerApp


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
    _request(app, "POST", "/api/designer/shape", {"shape": "hexagon"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    return app


def _full(payload):
    return next(
        tile for tile in payload["project"]["geometry"]["tiles"]
        if tile["piece_type"] == "full" and tile["editable"]
    )


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


def test_explicit_ivory_assignment_and_clear_reveal_lower_assignment():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    tile_id = _full(initial)["id"]
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "paint", "color_id": "project-color-3",
    })
    app.document_dirty = False

    _, restored = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "paint", "color_id": "project-color-1",
    })
    update = next(value for value in restored["tile_updates"] if value["id"] == tile_id)
    assert update["manual_override"] == "project-color-1"
    assert update["color_id"] == update["lower_color_id"] == "project-color-1"
    assert restored["document"]["dirty"] is True

    app.document_dirty = False
    _, cleared = _request(app, "POST", "/api/designer/paint/clear", {})
    assert cleared["paint"]["override_count"] == 0
    assert cleared["document"]["dirty"] is True


def test_every_visible_piece_is_editable_but_unknown_batch_is_atomic():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    valid = _full(initial)["id"]
    protected = next(
        tile["id"] for tile in initial["project"]["geometry"]["tiles"]
        if tile["piece_type"] != "full"
    )

    status, painted = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [valid, protected], "mode": "paint",
        "color_id": "project-color-2",
    })
    assert status == "200 OK"
    assert app.paint_overrides == {
        valid: "project-color-2", protected: "project-color-2",
    }
    assert painted["paint"]["override_count"] == 2

    status, _ = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [valid, "missing"], "mode": "paint",
        "color_id": "project-color-1",
    })
    assert status == "400 Bad Request"
    assert app.paint_overrides == {
        valid: "project-color-2", protected: "project-color-2",
    }


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
@pytest.mark.parametrize("across,down", (
    (1, 1), (2, 2), (3, 3), (5, 5), (5, 3), (3, 5), (10, 10),
))
def test_every_custom_visible_piece_is_paint_resolvable(orientation, across, down):
    payload = DesignerProjectShell.create_custom(
        "l", orientation, across, down,
    ).to_dict()
    visible = payload["geometry"]["tiles"]
    assert len(visible) == payload["geometry"]["visible_piece_count"]
    assert len({tile["id"] for tile in visible}) == len(visible)
    assert all(tile["editable"] is True for tile in visible)
    assert sum(tile["principal_grid"] for tile in visible) == across * down
    assert all(
        tile["piece_type"] == "full"
        for tile in visible if tile["principal_grid"]
    )
    assert sum(value["count"] for value in payload["color_counts"]) == len(visible)


def test_flat_top_bold_custom_5_by_5_edge_pieces_paint_and_erase():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "flat_top",
    })
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "l", "orientation": "flat_top",
    })
    _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "custom", "tiles_across": 5, "tiles_down": 5,
    })
    _, initial = _request(app, "GET", "/api/designer")
    visible = initial["project"]["geometry"]["tiles"]
    assert len(visible) == 45
    assert sum(tile["principal_grid"] for tile in visible) == 25
    ids = [
        tile["id"] for tile in visible
        if tile["principal_grid"]
        and tile["principal_row"] == 4
        and tile["principal_column"] in {1, 3}
    ]
    assert len(ids) == 2
    restored_bottom = [_tile(initial, tile_id) for tile_id in ids]
    assert all(tile["piece_type"] == "full" for tile in restored_bottom)
    assert all(tile["principal_grid"] for tile in restored_bottom)
    assert all(tile["editable"] for tile in restored_bottom)

    status, painted = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": ids, "mode": "paint", "color_id": "project-color-2",
    })
    assert status == "200 OK"
    assert painted["paint"]["overrides"] == dict.fromkeys(ids, "project-color-2")
    status, erased = _request(app, "POST", "/api/designer/paint/clear", {})
    assert status == "200 OK"
    assert erased["paint"]["overrides"] == {}


def test_manual_paint_has_precedence_across_every_border_change():
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
    for preset in ("solid", "double", "alternating", "none"):
        _request(app, "POST", "/api/designer/border", {"preset_id": preset})
        _, masked = _request(app, "GET", "/api/designer")
        tile = _tile(masked, border_tile["id"])
        assert tile["manual_override"] == "project-color-3"
        assert tile["color_id"] == "project-color-3"
        assert tile["editable"] is True

    _request(app, "POST", "/api/designer/border", {"preset_id": "solid"})
    _, masked = _request(app, "GET", "/api/designer")
    tile = _tile(masked, border_tile["id"])
    assert tile["manual_override"] == "project-color-3"
    assert tile["color_id"] == "project-color-3"

    _request(app, "POST", "/api/designer/paint/clear", {})
    _, restored = _request(app, "GET", "/api/designer")
    assert _tile(restored, border_tile["id"])["color_id"] == "project-color-2"


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


@pytest.mark.parametrize("orientation", ("flat_top", "point_top"))
def test_half_and_triangle_perimeter_pieces_paint_erase_and_clear(orientation):
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {"shape": "hexagon"})
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": orientation,
    })
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _, initial = _request(app, "GET", "/api/designer")
    partials = initial["project"]["geometry"]["tiles"]
    half = next(tile for tile in partials if tile["piece_type"] == "half")
    triangle = next(
        tile for tile in partials
        if tile["piece_type"] == "edge_cut"
        and tile["piece_fraction"] == pytest.approx(1 / 6)
    )
    polygons = {tile["id"]: tile["vertices_in"] for tile in (half, triangle)}
    assert all(tile["editable"] for tile in (half, triangle))
    assert all(tile["full_vertices_in"] for tile in (half, triangle))

    status, _ = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [half["id"], triangle["id"]],
        "mode": "paint", "color_id": "project-color-2",
    })
    assert status == "200 OK"
    _, painted = _request(app, "GET", "/api/designer")
    for original in (half, triangle):
        current = _tile(painted, original["id"])
        assert current["color_id"] == "project-color-2"
        assert current["vertices_in"] == polygons[original["id"]]
    assert sum(value["count"] for value in painted["project"]["color_counts"]) == (
        painted["project"]["geometry"]["visible_piece_count"]
    )

    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [half["id"]], "mode": "paint", "color_id": "project-color-1",
    })
    _, erased = _request(app, "GET", "/api/designer")
    assert _tile(erased, half["id"])["manual_override"] == "project-color-1"
    assert _tile(erased, triangle["id"])["manual_override"] == "project-color-2"
    _request(app, "POST", "/api/designer/paint/clear", {})
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
    assert 'id="paint-mode-restore"' not in html
    assert '>Erase</button>' not in html
    assert 'id="paint-assign"' not in html
    assert 'id="design-palette"' in html
    assert 'id="tiles-heading">Tiles<' in html
    assert 'id="paint-clear"' in html
    assert '>Clear Edits</button>' in html
    assert "window.confirm" not in script
    assert 'new Set()' in script
    assert '"/api/designer/paint"' in script
    assert "originalFills" in script
    assert "partial-parent-ghost" in script
    assert "partial-parent-hit" in script
    assert "full_vertices_in" in script
    assert "elementsFromPoint" in script
    assert "localeCompare" in script
    assert "resolvePartialTarget" in script
    assert "updatePartialPreview" in script
    assert "showPartialPreview" in script
    assert "hidePartialPreview" in script
    assert 'addEventListener("pointerleave", hidePartialPreview)' in script
    assert "pointerup" in script
    assert "hex_geometry" not in script
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert ".tile-color-swatch" in stylesheet
    assert ".paint-colors.flat-top .tile-color-swatch" in stylesheet
    assert '"flat-top", state.project.tile_orientation === "flat_top"' in script


def test_tiles_exposes_exactly_32_canonical_colors_without_slots_or_restore():
    app = _app()
    _, payload = _request(app, "GET", "/api/designer")
    paint = payload["project"]["paint"]
    assert "slots" not in paint
    assert len(paint["curated_palette"]) == 32
    assert [color["color_id"] for color in paint["curated_palette"]] == [
        f"project-color-{index}" for index in range(1, 33)
    ]
    assert len({color["display_color"] for color in paint["curated_palette"]}) == 32
    assert payload["project"]["color_counts"] == [{
        "color_id": "project-color-1",
        "display_color": "#FAF9F6",
        "name": "Ivory",
        "count": payload["project"]["geometry"]["visible_piece_count"],
        "order": 0,
    }]


def test_direct_canonical_assignment_is_compact_and_updates_counts():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    ids = [tile["id"] for tile in initial["project"]["geometry"]["tiles"][:2]]
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": ids, "mode": "paint", "color_id": "project-color-5",
    })
    assert app.paint_overrides == dict.fromkeys(ids, "project-color-5")
    before_geometry = app.project.geometry
    status, changed = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": ids, "mode": "paint", "color_id": "project-color-1",
    })
    assert status == "200 OK"
    assert "geometry" not in changed
    assert app.project.geometry is before_geometry
    assert app.paint_overrides == dict.fromkeys(ids, "project-color-1")
    assert {update["id"] for update in changed["tile_updates"]} == set(ids)
    assert all(update["display_color"] == "#FAF9F6" for update in changed["tile_updates"])
    counts = {value["display_color"]: value["count"] for value in changed["color_counts"]}
    assert "#466984" not in counts


def test_direct_tile_assignment_does_not_recolor_border():
    app = _app()
    _request(app, "POST", "/api/designer/border", {"preset_id": "alternating"})
    _, initial = _request(app, "GET", "/api/designer")
    gray_border = next(
        tile for tile in initial["project"]["geometry"]["tiles"]
        if tile["border_owned"] and tile["color_id"] == "project-color-3"
    )
    painted = next(
        tile for tile in initial["project"]["geometry"]["tiles"]
        if not tile["border_owned"]
    )
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [painted["id"]], "mode": "paint",
        "color_id": "project-color-5",
    })
    _, current = _request(app, "GET", "/api/designer")
    assert _tile(current, painted["id"])["display_color"] == "#466984"
    assert _tile(current, gray_border["id"])["display_color"] == "#808080"
    assert _tile(current, gray_border["id"])["manual_override"] is None


def test_legacy_slot_override_resolves_but_new_edit_is_direct_canonical_color():
    app = _app()
    _, initial = _request(app, "GET", "/api/designer")
    tile_id = _full(initial)["id"]
    app.paint_overrides[tile_id] = "paint-slot-3"
    _, legacy = _request(app, "GET", "/api/designer")
    assert _tile(legacy, tile_id)["color_id"] == "project-color-3"
    assert _tile(legacy, tile_id)["manual_override"] == "project-color-3"
    assert legacy["project"]["paint"]["overrides"][tile_id] == "project-color-3"
    status, _ = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "paint", "color_id": "project-color-5",
    })
    assert status == "200 OK"
    assert app.paint_overrides[tile_id] == "project-color-5"


def test_manual_ivory_overrides_artwork_until_clear_edits(monkeypatch):
    monkeypatch.setattr(generation_module, "SVG_RASTER_WIDTH", 256)
    app = _app()
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#000"/></svg>'
    _request(app, "POST", "/api/designer/artwork/upload", {
        "filename": "black.svg", "svg_content": svg,
    })
    _request(app, "POST", "/api/designer/artwork/generate", {})
    _, generated = _request(app, "GET", "/api/designer")
    tile = next(
        value for value in generated["project"]["geometry"]["tiles"]
        if value["generated_artwork"] and value["color_id"] != "project-color-1"
    )
    artwork_color = tile["color_id"]
    _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile["id"]], "mode": "paint",
        "color_id": "project-color-1",
    })
    _, white = _request(app, "GET", "/api/designer")
    assert _tile(white, tile["id"])["manual_override"] == "project-color-1"
    assert _tile(white, tile["id"])["color_id"] == "project-color-1"
    _request(app, "POST", "/api/designer/paint/clear", {})
    _, cleared = _request(app, "GET", "/api/designer")
    assert _tile(cleared, tile["id"])["color_id"] == artwork_color


def test_custom_mid_side_piece_accepts_direct_canonical_assignment():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/shape", {
        "shape": "hexagon", "orientation": "point_top",
    })
    _request(app, "POST", "/api/designer/tile", {
        "tile_id": "m", "orientation": "point_top",
    })
    _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "custom", "tiles_across": 10, "tiles_down": 10,
    })
    _, initial = _request(app, "GET", "/api/designer")
    piece = next(
        tile for tile in initial["project"]["geometry"]["tiles"]
        if tile["piece_type"] == "edge_cut"
        and not any(abs(tile["piece_fraction"] - fraction) < 1e-6 for fraction in (1 / 6, 1 / 2))
    )
    status, _ = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [piece["id"]], "mode": "paint",
        "color_id": "project-color-4",
    })
    assert status == "200 OK"
    assert app.paint_overrides[piece["id"]] == "project-color-4"


def test_parent_preview_is_below_physical_tiles_and_paint_gated():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert script.index("svg.appendChild(partialAidLayer)") < script.index(
        "svg.appendChild(baseLayer)"
    ) < script.index("svg.appendChild(protectedLayer)")
    assert script.index("svg.appendChild(boundary)") < script.index(
        "svg.appendChild(tileHitLayer)"
    ) < script.index("renderArtwork(svg, project.artwork)")
    assert "overflow: visible" in stylesheet
    assert ".partial-parent-ghost { fill: none" in stylesheet
    assert "#mosaic-canvas.paint-active .partial-parent-ghost.visible" in stylesheet
    assert "if (paintTool === null || !tileId) return" in script
    assert "if (paintTool === null) return hidePartialPreview()" in script
    assert ".panel-boundary { fill: none" in stylesheet
    assert "pointer-events: none" in stylesheet[stylesheet.index(".panel-boundary"):stylesheet.index(".tile-hit-layer")]


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
@pytest.mark.parametrize("across,down", ((5, 5), (10, 10)))
def test_every_custom_clipped_piece_exposes_full_parent_hit_geometry(
    orientation, across, down,
):
    shell = DesignerProjectShell.create_custom("m", orientation, across, down)
    payload = shell.to_dict()
    clipped = [
        tile for tile in payload["geometry"]["tiles"]
        if tile["piece_type"] != "full"
    ]
    assert clipped
    assert all(tile["full_vertices_in"] for tile in clipped)
    assert all(tile["parent_center_in"] for tile in clipped)
    assert all(tile["full_vertices_in"] != tile["vertices_in"] for tile in clipped)
    assert any(
        x < 0 or y < 0
        or x > payload["geometry"]["width_in"]
        or y > payload["geometry"]["height_in"]
        for tile in clipped for x, y in tile["full_vertices_in"]
    )


def test_partial_target_resolution_prioritizes_physical_then_nearest_then_id():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    resolver = script[
        script.index("function resolvePartialTarget"):
        script.index("function showPartialPreview")
    ]
    assert "if (actual)" in resolver
    assert "target = actual" in resolver
    assert "leftDistance - rightDistance" in resolver
    assert "left.dataset.tileId.localeCompare(right.dataset.tileId)" in resolver


def test_every_visible_piece_gets_an_exact_top_level_paint_hit_polygon():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    _, stylesheet = _request(app, "GET", "/designer.css")
    render = script[
        script.index("function renderWorkspace()"):
        script.index("function renderWorkspaceStatus")
    ]
    assert 'tileHitLayer.classList.add("tile-hit-layer")' in render
    assert 'paintHit.classList.add("tile-paint-hit", "editable")' in render
    assert "paintHit.dataset.tileId = tile.id" in render
    assert 'paintHit.setAttribute("points", polygon.getAttribute("points"))' in render
    assert "tileHitLayer.appendChild(paintHit)" in render
    assert ".tile-paint-hit.editable" in script
    assert ".tile-paint-hit { fill: transparent; stroke: none; pointer-events: none; }" in stylesheet
    assert "#mosaic-canvas.paint-active .tile-paint-hit { pointer-events: all; }" in stylesheet

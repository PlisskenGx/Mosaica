from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
import json
from math import isclose, sqrt
import sys
from wsgiref.util import setup_testing_defaults

import pytest

import mosaic_engine.designer as designer_module
from mosaic_engine.cli import main
from mosaic_engine.designer import (
    CANVAS_PRESETS,
    CANVAS_PREVIEW_REM_PER_INCH,
    DESIGNER_GROUT_MM,
    MM_PER_INCH,
    P1S_BUILD_AREA_MM,
    TILE_PRESETS,
    DesignerProjectShell,
    DesignerServerHandler,
    ThreadingWSGIServer,
    MosaicDesignerApp,
    estimate_minimum_print_plates,
    run_designer,
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
    if captured["headers"]["Content-Type"].startswith("application/json"):
        response = json.loads(response)
    else:
        response = response.decode()
    return captured["status"], response


def test_all_canvas_presets_have_exact_fixed_dimensions():
    assert [(value.id, value.name, value.width_in, value.height_in) for value in CANVAS_PRESETS] == [
        ("square-s", "Square S", 24.0, 24.0),
        ("square-m", "Square M", 36.0, 36.0),
        ("square-l", "Square L", 48.0, 48.0),
        ("landscape", "Landscape", 48.0, 30.0),
        ("wide", "Wide", 60.0, 30.0),
        ("panoramic", "Panoramic", 72.0, 30.0),
    ]


def test_tile_presets_are_flat_to_flat_with_fixed_physical_grout():
    assert [value.flat_to_flat_mm for value in TILE_PRESETS] == [20.0, 24.0, 28.0]
    assert DESIGNER_GROUT_MM == 1.8
    for value in TILE_PRESETS:
        assert isclose(value.flat_to_flat_in, value.flat_to_flat_mm / 25.4)
        assert isclose(value.side_length_mm, value.flat_to_flat_mm / sqrt(3))
    assert TILE_PRESETS[1].recommended is True


def test_medium_preset_converts_twenty_four_mm_flat_to_flat_to_engine_units():
    medium = TILE_PRESETS[1]
    assert isclose(medium.flat_to_flat_in, 24.0 / 25.4)
    assert isclose(medium.side_length_mm, 24.0 / sqrt(3))
    assert not isclose(medium.side_length_mm, 24.0)


def test_canvas_previews_share_one_physical_scale_and_preserve_aspect():
    payloads = [value.to_dict() for value in CANVAS_PRESETS]
    for preset, payload in zip(CANVAS_PRESETS, payloads):
        assert isclose(payload["preview_width_rem"], (
            preset.width_in * CANVAS_PREVIEW_REM_PER_INCH
        ))
        assert isclose(payload["preview_height_rem"], (
            preset.height_in * CANVAS_PREVIEW_REM_PER_INCH
        ))
        assert isclose(
            payload["preview_width_rem"] / payload["preview_height_rem"],
            payload["aspect_ratio"],
        )
    squares = payloads[:3]
    assert [value["preview_width_rem"] for value in squares] == [2.4, 3.6, 4.8]
    landscape, wide, panoramic = payloads[3:]
    assert landscape["aspect_ratio"] == 1.6
    assert wide["aspect_ratio"] == 2.0
    assert panoramic["aspect_ratio"] == 2.4
    assert [
        (value["preview_width_rem"], value["preview_height_rem"])
        for value in (landscape, wide, panoramic)
    ] == [(4.8, 3.0), (6.0, 3.0), (7.2, 3.0)]


@pytest.mark.parametrize("canvas_id,tile_id", [
    ("square-s", "s"),
    ("landscape", "m"),
    ("panoramic", "l"),
])
def test_representative_presets_create_nearest_vertex_constrained_geometry(canvas_id, tile_id):
    shell = DesignerProjectShell.create(canvas_id, tile_id)
    assert abs(shell.geometry.width_in - shell.canvas.width_in) < 1.0
    assert abs(shell.geometry.height_in - shell.canvas.height_in) < 1.0
    assert shell.geometry.shape == "hex"
    assert shell.geometry.orientation == "point_top"
    full = next(value for value in shell.geometry.placements if value.piece_type == "full")
    across_flats = max(x for x, _ in full.full_vertices_in) - min(
        x for x, _ in full.full_vertices_in
    )
    assert isclose(across_flats * MM_PER_INCH, shell.tile.flat_to_flat_mm, abs_tol=1e-8)


def test_blank_lattice_counts_and_payload_are_deterministic():
    first = DesignerProjectShell.create("landscape", "m").to_dict()
    second = DesignerProjectShell.create("landscape", "m").to_dict()
    assert first == second
    geometry = first["geometry"]
    assert geometry["full_tile_count"] > 0
    assert geometry["clipped_piece_count"] > 0
    assert geometry["visible_piece_count"] == (
        geometry["full_tile_count"] + geometry["clipped_piece_count"]
    )
    assert len(geometry["tiles"]) == geometry["visible_piece_count"]
    assert all(value["piece_type"] != "outside" for value in geometry["tiles"])
    assert all(value["vertices_in"] for value in geometry["tiles"])


def test_preset_selection_api_state_flow():
    app = MosaicDesignerApp()
    status, payload = _request(app, "GET", "/api/designer")
    assert status == "200 OK"
    assert payload["stage"] == "canvas"
    assert len(payload["canvas_presets"]) == 6
    assert len(payload["tile_presets"]) == 3
    assert payload["fixed_grout_mm"] == 1.8
    assert payload["document"] == {"title": "Untitled", "dirty": False}

    status, payload = _request(
        app, "POST", "/api/designer/canvas", {"canvas_id": "wide"},
    )
    assert status == "200 OK"
    assert payload["stage"] == "tile"
    assert payload["selected_canvas_id"] == "wide"
    assert payload["project"] is None
    assert payload["document"] == {"title": "Untitled", "dirty": False}

    status, payload = _request(
        app, "POST", "/api/designer/tile", {"tile_id": "m"},
    )
    assert status == "200 OK"
    assert payload["stage"] == "workspace"
    assert payload["project"]["canvas_preset"]["width_in"] == 60
    assert payload["project"]["canvas_preset"]["height_in"] == 30
    assert abs(payload["project"]["geometry"]["width_in"] - 60) < 1
    assert abs(payload["project"]["geometry"]["height_in"] - 30) < 1
    assert payload["project"]["tile_orientation"] == "point_top"
    assert payload["project"]["tile_preset"]["flat_to_flat_mm"] == 24
    assert payload["document"] == {"title": "Untitled", "dirty": False}

    status, payload = _request(app, "POST", "/api/designer/back", {})
    assert status == "200 OK"
    assert payload["stage"] == "tile"

    status, payload = _request(app, "POST", "/api/designer/back", {})
    assert status == "200 OK"
    assert payload["stage"] == "canvas"
    assert payload["selected_canvas_id"] is None


def test_tile_cannot_be_selected_before_canvas_and_presets_are_closed():
    app = MosaicDesignerApp()
    status, payload = _request(
        app, "POST", "/api/designer/tile", {"tile_id": "m"},
    )
    assert status == "400 Bad Request"
    assert "canvas" in payload["error"]
    status, payload = _request(
        app, "POST", "/api/designer/canvas", {"canvas_id": "custom"},
    )
    assert status == "400 Bad Request"
    assert "Unknown canvas preset" in payload["error"]


def test_designer_assets_use_backend_polygons_and_responsive_regions():
    app = MosaicDesignerApp()
    assert type(app).__name__ == "MosaicDesignerApp"
    status, html = _request(app, "GET", "/")
    assert status == "200 OK"
    assert "Mosaica" in html
    assert "by Veradura Design" in html
    assert "Mosaic Designer" not in html
    assert "<title>Mosaica</title>" in html
    assert "New mosaic" not in html
    assert "Physical tile system" not in html
    assert "Choose your canvas" in html
    assert "Choose your tile size" in html
    assert "canvas-presets" in html
    assert "tile-presets" in html
    assert "mosaic-canvas" in html
    assert 'id="canvas-viewport"' in html
    assert 'id="fit-workspace"' not in html
    assert "Fit to Workspace" not in html
    assert 'id="workspace-status"' in html
    assert 'id="document-name">Untitled' in html
    assert '<div class="app-bar-left">' in html
    assert '<button id="back" class="back-navigation" hidden>‹ Back</button>' in html
    assert html.index('class="brand"') < html.index('id="back"')
    assert html.index('id="back"') < html.index('id="document-title"')
    assert 'id="paint-heading">Paint<' in html
    assert "Physical canvas" not in html
    assert 'id="workspace-title"' not in html
    assert "Paint / Edit" not in html
    assert "Enter Paint" not in html
    assert "Colors" not in html
    assert "Coming later" not in html
    assert '<span class="brand-mark" aria-hidden="true"></span>' in html
    assert '<span class="brand-copy">' in html
    assert '<span class="brand-attribution">by Veradura Design</span>' in html
    assert '<svg class="brand-mark"' not in html
    assert "<polygon points=" not in html

    _, script = _request(app, "GET", "/designer.js")
    assert "geometry.tiles" in script
    assert "tile.vertices_in" in script
    assert "createElementNS" in script
    assert "hex_geometry" not in script
    assert "/api/designer/canvas" in script
    assert "/api/designer/tile" in script
    assert "/api/mosaica" not in script


def test_orientation_frontend_uses_scoped_label_helper_and_wires_both_choices():
    app = MosaicDesignerApp()
    _, script = _request(app, "GET", "/designer.js")
    assert 'const orientationLabel = (orientation)' in script
    assert 'orientation === "flat_top" ? "Flat Top" : "Point Top"' in script
    assert 'data-orientation="flat_top"' in script
    assert 'data-orientation="point_top"' in script
    assert 'chooseTile(preset.id, selectedOrientation)' in script
    assert 'chooseTile(tileId, orientation = "point_top")' in script
    assert script.count("const orientationName = orientationLabel(") == 1
    assert 'pointerenter' in script
    assert 'pointerleave' in script
    assert '"--preview-rotation"' in script


def test_workspace_and_sidebar_polish_is_structurally_scoped():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert html.index('id="artwork-heading"') < html.index('id="paint-heading"')
    assert html.index('id="paint-heading"') < html.index('id="border-heading"')
    for removed in (
        "Physical canvas", "Paint / Edit", "Enter Paint", "Restore",
        "Clear Paint Edits", "Coming later",
    ):
        assert removed not in html
    assert '>Erase</button>' in html
    assert '>Clear Edits</button>' in html
    assert " in actual`" not in script
    assert ".back-navigation" in stylesheet
    assert "font-size: .85rem" in stylesheet
    assert "font-weight: 400" in stylesheet
    assert "prefers-reduced-motion" in stylesheet
    assert "calculateFitSize" in script
    assert "fitToWorkspace" in script
    assert 'addEventListener("click", fitToWorkspace)' not in script
    assert "requestAnimationFrame(fitToWorkspace)" in script
    assert 'window.addEventListener("resize", fitToWorkspace)' in script
    assert "ResizeObserver" in script
    assert "preserveAspectRatio" in script
    assert "project.print_plate_estimate" in script
    assert "Est. ${plateEstimate.estimated_minimum_plates} plates" in script
    assert "Est. minimum:" not in script
    assert "preset.best_for" not in script
    assert "preset.tradeoff" not in script
    assert 'byId("back").hidden = stage === "canvas"' in script
    assert 'performDesignerMutation("/api/designer/back", {}, { name: "Back" })' in script

    _, stylesheet = _request(app, "GET", "/designer.css")
    assert "grid-template-columns: minmax(0, 1fr)" in stylesheet
    assert "@media (max-width: 680px)" in stylesheet
    assert ".artwork-object { cursor: move; touch-action: none; }" in stylesheet
    assert "height: calc(100dvh - var(--app-bar-height))" in stylesheet
    assert ".canvas-viewport" in stylesheet
    assert "min-height: 0" in stylesheet
    assert "overflow: hidden" in stylesheet
    assert ".workspace-status" in stylesheet
    assert "grid-column: 3" in stylesheet
    assert "justify-self: end" in stylesheet
    assert "border: 2px solid #303033" in stylesheet
    assert "border-radius: 38%" in stylesheet
    assert "transform: rotate(30deg)" in stylesheet
    assert ".app-bar-left { grid-column: 1" in stylesheet
    assert ".brand-attribution" in stylesheet
    assert ".brand-copy { display: flex; align-items: baseline" in stylesheet
    assert ".brand-copy strong { font-size: 1.2rem; }" in stylesheet
    assert ".brand-attribution { display: none; }" in stylesheet
    assert ".back-navigation" in stylesheet
    assert "border: 0" in stylesheet
    assert "background: transparent" in stylesheet
    assert "min-height: 2.75rem" in stylesheet
    assert ".back-navigation:focus-visible" in stylesheet
    assert ".quiet-button" not in stylesheet


@pytest.mark.parametrize("canvas_id,columns,rows,total", [
    ("square-s", 3, 3, 9),
    ("square-m", 4, 4, 16),
    ("square-l", 5, 5, 25),
    ("landscape", 5, 3, 15),
    ("wide", 6, 3, 18),
    ("panoramic", 8, 3, 24),
])
def test_p1s_rectangular_lower_bound_for_every_canvas(
    canvas_id, columns, rows, total,
):
    canvas = next(value for value in CANVAS_PRESETS if value.id == canvas_id)
    estimate = estimate_minimum_print_plates(canvas.width_in, canvas.height_in)
    assert estimate == {
        "build_area_mm": 256.0,
        "columns": columns,
        "rows": rows,
        "estimated_minimum_plates": total,
        "method": "rectangular lower bound",
    }
    assert P1S_BUILD_AREA_MM == 256.0


def test_plate_estimate_is_informational_and_does_not_mutate_geometry():
    shell = DesignerProjectShell.create("square-s", "m")
    placements_before = shell.geometry.placements
    geometry_before = shell.to_dict()["geometry"]
    estimate = estimate_minimum_print_plates(
        shell.geometry.width_in, shell.geometry.height_in,
    )
    assert estimate["estimated_minimum_plates"] == 9
    assert shell.geometry.placements == placements_before
    assert shell.to_dict()["geometry"] == geometry_before


def test_workspace_status_payload_contains_updated_physical_facts():
    payload = DesignerProjectShell.create("square-m", "m").to_dict()
    assert payload["canvas_preset"]["width_in"] == 36
    assert payload["tile_preset"]["flat_to_flat_mm"] == 24
    assert payload["grout_mm"] == 1.8
    assert payload["geometry"]["visible_piece_count"] > 0
    assert payload["print_plate_estimate"]["estimated_minimum_plates"] == 16


def test_designer_localhost_and_occupied_port_errors(monkeypatch):
    with pytest.raises(ValueError, match="localhost only"):
        run_designer(host="0.0.0.0", open_browser=False)
    def occupied(*args, **kwargs):
        raise OSError("occupied")
    monkeypatch.setattr(designer_module, "make_server", occupied)
    with pytest.raises(RuntimeError, match="requested port is unavailable"):
        run_designer(port=9876, open_browser=False)


def test_json_response_framing_matches_body_exactly():
    app = MosaicDesignerApp()
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/api/designer",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(),
    }, start_response))
    assert captured["status"] == "200 OK"
    assert captured["headers"]["Content-Type"] == (
        "application/json; charset=utf-8"
    )
    assert int(captured["headers"]["Content-Length"]) == len(body)
    assert "Transfer-Encoding" not in captured["headers"]
    assert json.loads(body)["stage"] == "canvas"


def test_threaded_http_server_writes_complete_framed_response(caplog):
    app = MosaicDesignerApp()
    environ = {}
    setup_testing_defaults(environ)
    environ.update({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/api/designer",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(),
    })
    output = BytesIO()
    handler = DesignerServerHandler(
        BytesIO(), output, StringIO(), environ, multithread=True,
    )
    handler.http_version = "1.1"
    with caplog.at_level("INFO", logger="mosaic_engine.designer.transport"):
        handler.run(app)
    headers, body = output.getvalue().split(b"\r\n\r\n", 1)
    assert headers.startswith(b"HTTP/1.1 200 OK")
    content_length = next(
        int(line.split(b":", 1)[1])
        for line in headers.split(b"\r\n")
        if line.lower().startswith(b"content-length:")
    )
    assert content_length == len(body)
    assert b"Connection: close" in headers
    assert json.loads(body)["stage"] == "canvas"
    assert "write_completed=True" in caplog.text
    assert "transfer_encoding=None" in caplog.text
    assert ThreadingWSGIServer.daemon_threads is True


def test_concurrent_designer_reads_and_mutations_remain_atomic():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})

    with ThreadPoolExecutor(max_workers=4) as executor:
        reads = list(executor.map(
            lambda _: _request(app, "GET", "/api/designer")[1],
            range(8),
        ))
        changes = list(executor.map(
            lambda preset: (
                preset,
                _request(
                    app, "POST", "/api/designer/border",
                    {"preset_id": preset},
                )[1],
            ),
            ("solid", "double", "alternating", "none"),
        ))

    assert all(value == reads[0] for value in reads)
    for requested, response in changes:
        assert response["border"]["preset_id"] == requested
        assert response["payload_kind"] == "design_state"
    final = app.payload()["project"]
    assert final["border"]["preset_id"] in {
        "solid", "double", "alternating", "none",
    }
    assert sum(value["count"] for value in final["color_counts"]) == (
        final["geometry"]["visible_piece_count"]
    )


def test_cli_launches_designer_without_changing_editor_behavior(monkeypatch):
    called = {}
    def fake_run_designer(**options):
        called.update(options)
    monkeypatch.setattr(designer_module, "run_designer", fake_run_designer)
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine", "--designer", "--editor-port", "9124", "--no-browser",
    ])
    main()
    assert called == {"port": 9124, "open_browser": False}

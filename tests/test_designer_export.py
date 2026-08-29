from io import BytesIO
import json
from pathlib import Path
from threading import Event
import time

import pytest

from mosaica.designer import DesignerProjectShell, MosaicDesignerApp
from mosaica.designer_export import (
    DesignerFabricationExportService,
    sanitize_export_name,
)
from mosaica.fabricate.modes import FabricationMode
from mosaica.fabricate.panelize import PanelizationError, panelize_model


def _request(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    route, _, query = path.partition("?")
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(app({
        "REQUEST_METHOD": method,
        "PATH_INFO": route,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": BytesIO(raw),
    }, start_response))
    if captured["headers"]["Content-Type"].startswith("application/json"):
        response = json.loads(response)
    else:
        response = response.decode()
    return captured["status"], response


def _app(tmp_path, *, preset="square", tile="l"):
    app = MosaicDesignerApp(export_root=tmp_path)
    app.project = DesignerProjectShell.create(preset, tile)
    return app


def _small_app(tmp_path, opener=None):
    app = MosaicDesignerApp(export_root=tmp_path, export_folder_opener=opener)
    app.project = DesignerProjectShell.create_custom("l", "point_top", 3, 3)
    return app


def test_export_action_dialog_and_mode_copy_are_in_designer_assets():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    assert 'id="export-action"' in html
    assert 'id="export-dialog"' in html
    assert 'data-export-mode="studio"' in html
    assert 'data-export-mode="museum"' in html
    assert "<strong>Studio</strong>" in html
    assert "Efficient production with fewer panels." in html
    assert "<strong>Fast</strong>" not in html
    assert 'class="export-mode-card selected"' in html
    assert "Adaptive Variable Layer Height recommended" in html
    assert "May produce fewer panels" in html
    assert "May require more panels" in html
    assert "openExportDialog" in script
    assert "loadExportPreview" in script
    assert "/api/designer/export/start" in script
    assert "exportMode = \"studio\"" in script
    assert "exportJobId = job.job_id" in script
    assert 'disabled aria-busy="true">Preparing Export…' in html
    assert 'id="export-progress-control"' in html
    assert "exportPreviewToken" in script
    assert "token !== exportPreviewToken || requestedMode !== exportMode" in script
    assert "exportPreviewReadyMode !== exportMode" in script
    assert 'setExportControl("error")' in script
    assert 'setExportControl("ready")' in script
    assert "if (exportInFlight) return" in script
    assert 'id="export-activity"' in html
    assert 'byId("export-activity").textContent = job.progress.message' in script
    assert "setInterval" not in script


def test_live_summary_uses_actual_mode_aware_panelization(tmp_path):
    app = _app(tmp_path)
    snapshot = app._export_snapshot()
    studio = app.export_service.preview(snapshot, FabricationMode.STUDIO)
    museum = app.export_service.preview(snapshot, FabricationMode.MUSEUM)
    assert (studio["rows"], studio["columns"], studio["panel_count"]) == (3, 3, 9)
    assert (museum["rows"], museum["columns"], museum["panel_count"]) == (4, 3, 12)
    assert studio["safe_envelope_mm"] == {"width": 228.0, "height": 228.0}
    assert museum["safe_envelope_mm"] == {"width": 210.0, "height": 210.0}
    assert studio["panel_count"] == len(panelize_model(
        app.export_service._model(snapshot), mode=FabricationMode.STUDIO,
    ).panels)


def test_preview_api_switches_modes_and_reports_current_project(tmp_path):
    app = _app(tmp_path)
    status, studio = _request(app, "POST", "/api/designer/export/preview", {"mode": "studio"})
    assert status == "200 OK"
    _, museum = _request(app, "POST", "/api/designer/export/preview", {"mode": "museum"})
    assert studio["mode"]["id"] == "studio"
    assert studio["mode"]["display_name"] == "Studio"
    assert studio["mode"]["recommended"] is True
    assert museum["mode"]["id"] == "museum"
    assert studio["tile"] == {
        "preset_id": "l", "flat_to_flat_mm": 28.0, "orientation": "point_top",
    }
    assert studio["finished_mosaic"]["width_in"] == pytest.approx(
        app.project.geometry.width_in,
    )


def test_current_border_and_paint_state_reaches_fabrication_resolution(tmp_path):
    app = _small_app(tmp_path)
    tile_id = app.project.to_dict()["geometry"]["tiles"][0]["id"]
    target_color = app.project.color_system.colors[2].color_id
    app.project = app.project.with_border("solid")
    app.paint_overrides = {tile_id: target_color}
    model = app.export_service._model(app._export_snapshot())
    resolved = next(
        tile for tile in model.tiles if tile.source_color_id == target_color
    )
    source = next(
        tile for tile in app.project.to_dict()["geometry"]["tiles"]
        if tile["id"] == tile_id
    )
    assert resolved.source_color_id == target_color
    assert resolved.center_mm[0] == pytest.approx(
        source["center_in"][0] * 25.4,
    )
    assert model.border_preset_id == "solid"


def test_unsaved_project_generates_complete_package_in_background(tmp_path):
    app = _small_app(tmp_path)
    status, job = _request(app, "POST", "/api/designer/export/start", {"mode": "studio"})
    assert status == "202 Accepted"
    assert job["status"] == "running"
    assert job["job_id"].startswith("export-")
    for _ in range(100):
        _, current = _request(app, "GET", f"/api/designer/export/status?id={job['job_id']}")
        if current["status"] != "running":
            break
        time.sleep(0.02)
    assert current["status"] == "complete"
    result = current["result"]
    output = Path(result["output_directory"])
    assert output.name == "Mosaica_Project"
    assert (output / "Mosaica_Print_Guide.pdf").is_file()
    assert (output / "manifest.json").is_file()
    assert len(list(output.glob("Mosaica_*.3mf"))) == result["panel_count"] == 1
    phases = [event["phase"] for event in current["progress_events"]]
    assert phases == [
        "resolving", "panelizing", "preparing_panels", "building_panel",
        "writing_manifest", "creating_print_guide", "finalizing",
    ]
    panel_event = current["progress_events"][3]
    assert panel_event == {
        "phase": "building_panel", "current_panel": "A1",
        "completed_panels": 0, "total_panels": 1, "panel_index": 1,
        "message": "Building Panel A1 · 1 of 1",
    }
    assert current["progress"]["phase"] == "complete"
    assert result["mode"] == "studio"
    assert result["mode_display_name"] == "Studio"


@pytest.mark.parametrize("mode,expected_count", [("studio", 9), ("museum", 12)])
def test_export_progress_reports_every_real_panel_id(
    tmp_path, mode, expected_count,
):
    app = _app(tmp_path / mode)
    snapshot = app._export_snapshot()
    preview = app.export_service.preview(snapshot, mode)
    events = []
    output = app.export_service.allocate_output_directory("Progress Fixture")
    app.export_service.generate(snapshot, mode, output, progress=events.append)
    panel_events = [
        event for event in events if event["phase"] == "building_panel"
    ]
    assert preview["panel_count"] == expected_count
    assert [event["current_panel"] for event in panel_events] == [
        panel["panel_id"] for panel in preview["panels"]
    ]
    assert [event["panel_index"] for event in panel_events] == list(
        range(1, expected_count + 1)
    )
    assert [event["total_panels"] for event in panel_events] == [
        expected_count,
    ] * expected_count
    assert [event["phase"] for event in events[-3:]] == [
        "writing_manifest", "creating_print_guide", "finalizing",
    ]


def test_export_collision_is_safe_and_existing_contents_survive(tmp_path):
    service = DesignerFabricationExportService(tmp_path)
    first = service.allocate_output_directory("My Project")
    sentinel = first / "keep.txt"
    sentinel.write_text("keep")
    second = service.allocate_output_directory("My Project")
    assert first.name == "Mosaica_My_Project"
    assert second.name == "Mosaica_My_Project_2"
    assert sentinel.read_text() == "keep"
    assert sanitize_export_name("A/B:*? Project") == "AB_Project"


def test_open_folder_is_limited_to_completed_export(tmp_path):
    opened = []
    app = _small_app(tmp_path, opened.append)
    output = app.export_service.allocate_output_directory("Untitled")
    result = app.export_service.generate(app._export_snapshot(), "studio", output)
    app._export_jobs["done"] = {
        "job_id": "done", "status": "complete", "result": result.to_dict(),
    }
    status, payload = _request(app, "POST", "/api/designer/export/open", {"id": "done"})
    assert status == "200 OK"
    assert payload["opened"] is True
    assert opened == [output.resolve()]


def test_panelization_and_filesystem_failures_are_actionable(tmp_path, monkeypatch):
    app = _small_app(tmp_path)
    monkeypatch.setattr(
        app.export_service, "preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PanelizationError("internal")),
    )
    status, payload = _request(app, "POST", "/api/designer/export/preview", {"mode": "studio"})
    assert status == "400 Bad Request"
    assert "printable panels" in payload["error"]
    assert "internal" not in payload["error"]

    app = _small_app(tmp_path)
    monkeypatch.setattr(
        app.export_service, "allocate_output_directory",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )
    status, payload = _request(app, "POST", "/api/designer/export/start", {"mode": "studio"})
    assert status == "400 Bad Request"
    assert "folder permissions" in payload["error"]


def test_generation_failure_stops_at_real_current_progress(tmp_path, monkeypatch):
    app = _small_app(tmp_path)

    def fail_during_panel(_snapshot, _mode, _output, progress):
        progress({
            "phase": "building_panel", "current_panel": "A1",
            "completed_panels": 0, "total_panels": 1, "panel_index": 1,
            "message": "Building Panel A1 · 1 of 1",
        })
        raise OSError("simulated write failure")

    monkeypatch.setattr(app.export_service, "generate", fail_during_panel)
    status, started = _request(
        app, "POST", "/api/designer/export/start", {"mode": "studio"},
    )
    assert status == "202 Accepted"
    for _ in range(100):
        _, job = _request(
            app, "GET", f"/api/designer/export/status?id={started['job_id']}",
        )
        if job["status"] != "running":
            break
        time.sleep(0.01)
    assert job["status"] == "error"
    assert job["progress"]["phase"] == "error"
    assert job["progress"]["current_panel"] == "A1"
    assert [event["phase"] for event in job["progress_events"]] == [
        "building_panel",
    ]
    assert "Writing manifest" not in " ".join(
        event["message"] for event in job["progress_events"]
    )


def test_export_preview_and_generation_do_not_mutate_designer_state(tmp_path):
    app = _small_app(tmp_path)
    before = app.project.to_dict(app.generated_artwork, app.paint_overrides)
    snapshot = app._export_snapshot()
    app.export_service.preview(snapshot, "studio")
    output = app.export_service.allocate_output_directory("Untitled")
    app.export_service.generate(snapshot, "studio", output)
    assert app.project.to_dict(app.generated_artwork, app.paint_overrides) == before


def test_duplicate_background_exports_are_blocked(tmp_path, monkeypatch):
    app = _small_app(tmp_path)
    original_generate = app.export_service.generate
    started = Event()
    release = Event()

    def delayed_generate(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return original_generate(*args, **kwargs)

    monkeypatch.setattr(app.export_service, "generate", delayed_generate)
    status, first = _request(
        app, "POST", "/api/designer/export/start", {"mode": "studio"},
    )
    assert status == "202 Accepted"
    assert started.wait(1)
    status, second = _request(
        app, "POST", "/api/designer/export/start", {"mode": "studio"},
    )
    assert status == "400 Bad Request"
    assert "already in progress" in second["error"]
    release.set()
    for _ in range(100):
        _, job = _request(
            app, "GET", f"/api/designer/export/status?id={first['job_id']}",
        )
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "complete"

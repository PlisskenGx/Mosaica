from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path

import pytest

import mosaic_engine.editor as editor_module
from mosaic_engine.editor import MosaicEditorApp, run_editor
from mosaic_engine.geometry import (
    build_geometry,
    build_panel_geometry,
)
from mosaic_engine.model import (
    MosaicConfig,
    MosaicResult,
    PaletteColor,
)
from mosaic_engine.project import MosaicProject


PALETTE = (
    PaletteColor("Black", (0, 0, 0)),
    PaletteColor("White", (255, 255, 255)),
)


def _save_project(tmp_path, geometry=None):
    config = MosaicConfig(columns=2, rows=1)
    geometry = geometry or build_geometry(config, 2, 1)
    result = MosaicResult(
        columns=geometry.columns,
        rows=geometry.rows,
        grid=[
            [0 for _ in range(geometry.columns)]
            for _ in range(geometry.rows)
        ],
        palette=PALETTE,
        source_path=tmp_path / "missing-source.png",
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    )
    path = tmp_path / "project.json"
    MosaicProject.from_result(result).save(path)
    return path


def _request(app, method, path, body=None):
    raw = (
        json.dumps(body).encode("utf-8")
        if body is not None
        else b""
    )
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(
        app(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "CONTENT_LENGTH": str(len(raw)),
                "wsgi.input": BytesIO(raw),
            },
            start_response,
        )
    )
    content_type = captured["headers"].get("Content-Type", "")
    value = (
        json.loads(response)
        if content_type.startswith("application/json")
        else response.decode("utf-8")
    )
    return captured["status"], value


def test_editor_route_loads_saved_project(tmp_path):
    app = MosaicEditorApp(_save_project(tmp_path))

    status, body = _request(app, "GET", "/")

    assert status == "200 OK"
    assert "Mosaic Engine Editor" in body
    assert "editor.js" in body

    status, script = _request(app, "GET", "/editor.js")
    assert status == "200 OK"
    assert "beforeunload" in script
    assert "selectedIds.has(tile.id)" in script
    assert "event.shiftKey" in script
    assert "selectedIds.clear()" in script
    assert "selectedIds.add(tile.id)" in script
    assert "selectedIds.delete(tile.id)" in script
    assert 'event.key >= "1" && event.key <= "9"' in script
    assert 'event.key.toLowerCase() === "x"' in script
    assert 'event.key === "Escape"' in script
    assert "isEditableControl(event.target)" in script
    assert "input, textarea, select" in script
    assert 'request("/api/overrides/batch"' in script
    assert 'request("/api/overrides/batch-clear"' in script

    status, stylesheet = _request(app, "GET", "/editor.css")
    assert status == "200 OK"
    assert ".tile.tile.selected" in stylesheet
    assert ".tile.editable:hover" in stylesheet
    assert ".tile.protected:hover" in stylesheet
    assert "paint-order: stroke fill" in stylesheet


def test_editor_loads_and_exports_state_without_source(tmp_path):
    path = _save_project(tmp_path)
    assert not (tmp_path / "missing-source.png").exists()

    app = MosaicEditorApp(path)
    status, payload = _request(app, "GET", "/api/project")

    assert status == "200 OK"
    assert payload["project"]["source_filename"] == "missing-source.png"


def test_project_api_returns_geometry_assignments_and_editability(tmp_path):
    geometry = build_geometry(MosaicConfig(columns=2, rows=1), 2, 1)
    placements = list(geometry.placements)
    placements[1] = replace(
        placements[1],
        piece_type="outside",
        piece_fraction=0.0,
        vertices_in=(),
    )
    geometry = replace(geometry, placements=tuple(placements))
    app = MosaicEditorApp(_save_project(tmp_path, geometry))

    status, payload = _request(app, "GET", "/api/project")

    assert status == "200 OK"
    assert payload["panel"] == {"width_in": 2.0, "height_in": 1.0}
    assert len(payload["palette"]) == 2
    assert payload["counts"] == {"Black": 1, "White": 0}
    assert len(payload["tiles"]) == 1
    tile = payload["tiles"][0]
    assert tile["vertices_in"]
    assert tile["generated_index"] == 0
    assert tile["override_index"] is None
    assert tile["effective_index"] == 0
    assert tile["editable"] is True


def test_tile_ids_are_stable_unique_and_separate_from_coordinates(tmp_path):
    path = _save_project(tmp_path)
    first_app = MosaicEditorApp(path)
    second_app = MosaicEditorApp(path)
    _, first = _request(first_app, "GET", "/api/project")
    _, second = _request(second_app, "GET", "/api/project")
    first_ids = [tile["id"] for tile in first["tiles"]]
    second_ids = [tile["id"] for tile in second["tiles"]]

    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids))
    assert first_ids == ["placement-000000", "placement-000001"]
    assert all(
        tile["id"] != f"tile-{tile['row']}-{tile['column']}"
        for tile in first["tiles"]
    )


def test_api_sets_and_clears_override(tmp_path):
    app = MosaicEditorApp(_save_project(tmp_path))

    status, payload = _request(
        app,
        "POST",
        "/api/tiles/0/0/override",
        {"palette_index": 1},
    )
    assert status == "200 OK"
    tile = next(
        tile
        for tile in payload["tiles"]
        if (tile["row"], tile["column"]) == (0, 0)
    )
    assert tile["generated_index"] == 0
    assert tile["override_index"] == 1
    assert tile["effective_index"] == 1
    assert payload["counts"] == {"Black": 1, "White": 1}
    assert payload["dirty"] is True

    status, payload = _request(
        app,
        "POST",
        "/api/tiles/0/0/clear",
        {},
    )
    assert status == "200 OK"
    tile = next(
        tile
        for tile in payload["tiles"]
        if (tile["row"], tile["column"]) == (0, 0)
    )
    assert tile["override_index"] is None
    assert tile["effective_index"] == 0
    assert payload["dirty"] is True


def test_batch_override_updates_all_tiles_and_counts(tmp_path):
    app = MosaicEditorApp(_save_project(tmp_path))

    status, payload = _request(
        app,
        "POST",
        "/api/overrides/batch",
        {
            "tile_ids": ["placement-000000", "placement-000001"],
            "palette_index": 1,
        },
    )

    assert status == "200 OK"
    assert payload["dirty"] is True
    assert payload["counts"] == {"Black": 0, "White": 2}
    assert all(tile["override_index"] == 1 for tile in payload["tiles"])
    assert all(tile["effective_index"] == 1 for tile in payload["tiles"])


def test_batch_clear_restores_generated_values(tmp_path):
    app = MosaicEditorApp(_save_project(tmp_path))
    tile_ids = ["placement-000000", "placement-000001"]
    _request(
        app,
        "POST",
        "/api/overrides/batch",
        {"tile_ids": tile_ids, "palette_index": 1},
    )

    status, payload = _request(
        app,
        "POST",
        "/api/overrides/batch-clear",
        {"tile_ids": tile_ids},
    )

    assert status == "200 OK"
    assert payload["counts"] == {"Black": 2, "White": 0}
    assert all(tile["override_index"] is None for tile in payload["tiles"])
    assert all(tile["effective_index"] == 0 for tile in payload["tiles"])


def test_invalid_batch_is_atomic_and_does_not_mark_dirty(tmp_path):
    config = MosaicConfig(
        tile_shape="hex",
        target_width_in=3,
        target_height_in=2,
    )
    geometry = build_panel_geometry(config, 3, 2)
    app = MosaicEditorApp(_save_project(tmp_path, geometry))
    _, initial = _request(app, "GET", "/api/project")
    editable = next(tile for tile in initial["tiles"] if tile["editable"])
    protected = next(tile for tile in initial["tiles"] if not tile["editable"])

    status, error = _request(
        app,
        "POST",
        "/api/overrides/batch",
        {
            "tile_ids": [editable["id"], protected["id"]],
            "palette_index": 1,
        },
    )

    assert status == "400 Bad Request"
    assert "protected" in error["error"]
    assert app.project.override_value(
        editable["row"],
        editable["column"],
    ) is None
    assert app.dirty is False

    app.project.set_override(
        editable["row"],
        editable["column"],
        1,
    )
    status, error = _request(
        app,
        "POST",
        "/api/overrides/batch-clear",
        {"tile_ids": [editable["id"], protected["id"]]},
    )

    assert status == "400 Bad Request"
    assert "protected" in error["error"]
    assert app.project.override_value(
        editable["row"],
        editable["column"],
    ) == 1
    assert app.dirty is False


def test_noop_batch_does_not_mark_clean_project_dirty(tmp_path):
    path = _save_project(tmp_path)
    project = MosaicProject.load(path)
    project.set_override(0, 0, 1)
    project.save(path)
    app = MosaicEditorApp(path)

    status, payload = _request(
        app,
        "POST",
        "/api/overrides/batch",
        {
            "tile_ids": ["placement-000000"],
            "palette_index": 1,
        },
    )

    assert status == "200 OK"
    assert payload["dirty"] is False


def test_api_rejects_protected_perimeter_edits(tmp_path):
    config = MosaicConfig(
        tile_shape="hex",
        target_width_in=3,
        target_height_in=2,
    )
    geometry = build_panel_geometry(config, 3, 2)
    app = MosaicEditorApp(_save_project(tmp_path, geometry))
    _, payload = _request(app, "GET", "/api/project")
    clipped = next(
        tile
        for tile in payload["tiles"]
        if tile["piece_type"] != "full"
    )

    status, error = _request(
        app,
        "POST",
        f"/api/tiles/{clipped['row']}/{clipped['column']}/override",
        {"palette_index": 1},
    )

    assert status == "400 Bad Request"
    assert clipped["editable"] is False
    assert "protected" in error["error"]


def test_api_save_persists_overrides(tmp_path):
    path = _save_project(tmp_path)
    app = MosaicEditorApp(path)
    _request(
        app,
        "POST",
        "/api/tiles/0/0/override",
        {"palette_index": 1},
    )

    status, payload = _request(app, "POST", "/api/save", {})

    assert status == "200 OK"
    assert payload["saved"] is True
    assert payload["dirty"] is False
    assert app.dirty is False
    assert MosaicProject.load(path).override_value(0, 0) == 1


def test_api_clear_all_overrides(tmp_path):
    app = MosaicEditorApp(_save_project(tmp_path))
    _request(
        app,
        "POST",
        "/api/tiles/0/0/override",
        {"palette_index": 1},
    )
    _request(
        app,
        "POST",
        "/api/tiles/0/1/override",
        {"palette_index": 1},
    )

    status, payload = _request(
        app,
        "POST",
        "/api/overrides/clear-all",
        {},
    )

    assert status == "200 OK"
    assert payload["overrides_count"] == 0
    assert payload["dirty"] is True
    assert all(tile["override_index"] is None for tile in payload["tiles"])


def test_editor_rejects_non_local_bind_address():
    with pytest.raises(ValueError, match="localhost only"):
        run_editor(
            "unused.json",
            host="0.0.0.0",
            open_browser=False,
        )


def test_editor_reports_occupied_port(tmp_path, monkeypatch):
    project_path = _save_project(tmp_path)
    port = 9123

    def occupied_port(*args, **kwargs):
        raise OSError("Address already in use")

    monkeypatch.setattr(editor_module, "make_server", occupied_port)

    with pytest.raises(
        RuntimeError,
        match=rf"127\.0\.0\.1:{port}.*unavailable",
    ):
        run_editor(
            project_path,
            port=port,
            open_browser=False,
        )

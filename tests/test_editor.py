from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image

import mosaica.editor as editor_module
from mosaica.editor import MosaicEditorApp, run_editor
from mosaica.contour_refinement import (
    ContourAlternative,
    ContourCandidate,
    ContourRefinementReport,
    ContourScore,
)
from mosaica.evidence import cache_project_bw_evidence
from mosaica.geometry import (
    build_geometry,
    build_panel_geometry,
)
from mosaica.model import (
    MosaicConfig,
    MosaicResult,
    PaletteColor,
)
from mosaica.project import MosaicProject
from mosaica.refinement import (
    CandidateRegion,
    RefinementProposal,
    RefinementReport,
    ScoreBreakdown,
    TileChange,
)


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


def _proposal_report(*changes, candidate_id="region-0001"):
    score = ScoreBreakdown(1, 1, 1, -1, 1, 0, 0)
    tile_changes = tuple(
        TileChange(
            tile_id=f"placement-{row * 2 + column:06d}",
            row=row,
            column=column,
            generated_index=generated,
            proposed_index=proposed,
        )
        for row, column, generated, proposed in changes
    )
    proposal = RefinementProposal(
        candidate_id=candidate_id,
        rank=1,
        alternative="expand",
        affected_tile_ids=tuple(change.tile_id for change in tile_changes),
        changes=tile_changes,
        baseline_score=3.0,
        alternative_score=3.5,
        baseline_breakdown=score,
        alternative_breakdown=replace(score, source_agreement=1.5),
        reason="synthetic diagonal boundary",
    )
    candidate = CandidateRegion(
        candidate_id=candidate_id,
        coordinates=tuple((change.row, change.column) for change in tile_changes),
        tile_ids=proposal.affected_tile_ids,
        reasons=("synthetic diagonal boundary",),
        alternatives=("retain", "expand"),
    )
    return RefinementReport(
        candidates=(candidate,),
        proposals=(proposal,),
    )


def _contour_report():
    score = ContourScore(1, 1, 1, -0.2, 0, -0.1)
    alternative = ContourAlternative(
        name="source-trajectory",
        rank=1,
        path=((0, 0), (0, 1)),
        proposed_contour=((0.5, 0.5), (1.5, 0.5)),
        changes=(),
        score=score,
        score_delta=0.1,
        is_recommended=True,
    )
    return ContourRefinementReport(candidates=(ContourCandidate(
        candidate_id="contour-0001",
        reason="synthetic continuous contour",
        region=((0, 0), (0, 1)),
        affected_tile_ids=("placement-000000", "placement-000001"),
        source_contour=((0.4, 0.5), (1.4, 0.5)),
        current_mosaic_contour=((0.5, 0.5), (1.5, 0.5)),
        baseline_score=replace(score, source_trajectory_agreement=0.9),
        alternatives=(alternative,),
        recommended_alternative="source-trajectory",
    ),))


def test_editor_route_loads_saved_project(tmp_path):
    app = MosaicEditorApp(_save_project(tmp_path))

    status, body = _request(app, "GET", "/")

    assert status == "200 OK"
    assert "Mosaica Editor" in body
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
    assert 'request("/api/proposals"' in script
    assert 'request("/api/contour-proposals"' in script
    assert "contour-source" in script
    assert "contour-current" in script
    assert "contour-proposed" in script
    assert "proposal-addition" in script
    assert "proposal-removal" in script
    assert 'event.key === "["' in script
    assert 'event.key === "]"' in script
    assert 'event.key === "Enter" && currentProposal()' in script
    assert 'event.key.toLowerCase() === "r"' in script
    assert 'event.key.toLowerCase() === "s"' in script
    assert "isEditableControl(event.target)" in script

    status, stylesheet = _request(app, "GET", "/editor.css")
    assert status == "200 OK"
    assert ".tile.tile.selected" in stylesheet
    assert ".tile.editable:hover" in stylesheet
    assert ".tile.protected:hover" in stylesheet
    assert "paint-order: stroke fill" in stylesheet
    assert ".tile.proposal-addition" in stylesheet
    assert ".tile.proposal-removal" in stylesheet
    assert ".tile.manual-override" in stylesheet
    assert ".contour-source" in stylesheet
    assert ".contour-current" in stylesheet
    assert ".contour-proposed" in stylesheet


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


def test_proposal_list_and_detail_api_are_deterministic(tmp_path):
    report = _proposal_report((0, 0, 0, 1))
    app = MosaicEditorApp(
        _save_project(tmp_path), refinement_report=report
    )

    first_status, first = _request(app, "GET", "/api/proposals")
    second_status, second = _request(app, "GET", "/api/proposals")
    detail_status, detail = _request(
        app, "GET", "/api/proposals/region-0001"
    )

    assert first_status == second_status == detail_status == "200 OK"
    assert first == second
    assert first["candidates"][0]["candidate_id"] == "region-0001"
    assert detail["ranked_alternatives"][0]["rank"] == 1
    assert detail["ranked_alternatives"][0]["changes"][0] == {
        "tile_id": "placement-000000",
        "row": 0,
        "column": 0,
        "generated_index": 0,
        "proposed_index": 1,
        "change_kind": "foreground_removal",
    }


def test_proposal_preview_endpoints_do_not_mutate_project(tmp_path):
    app = MosaicEditorApp(
        _save_project(tmp_path),
        refinement_report=_proposal_report((0, 0, 0, 1)),
    )
    generated = app.project.generated_grid
    overrides = app.project.overrides

    _request(app, "GET", "/api/proposals")
    _request(app, "GET", "/api/proposals/region-0001")

    assert app.project.generated_grid == generated
    assert app.project.overrides == overrides
    assert app.dirty is False


def test_accept_proposal_applies_sparse_overrides_once(tmp_path):
    app = MosaicEditorApp(
        _save_project(tmp_path),
        refinement_report=_proposal_report(
            (0, 0, 0, 1),
            (0, 1, 0, 1),
        ),
    )

    status, payload = _request(
        app,
        "POST",
        "/api/proposals/region-0001/expand/accept",
        {},
    )

    assert status == "200 OK"
    assert app.project.generated_grid == ((0, 0),)
    assert app.project.overrides == {(0, 0): 1, (0, 1): 1}
    assert payload["project"]["dirty"] is True
    assert payload["session"]["accepted"] == 1


def test_accept_preserves_manual_overrides_outside_proposal(tmp_path):
    app = MosaicEditorApp(
        _save_project(tmp_path),
        refinement_report=_proposal_report((0, 0, 0, 1)),
    )
    app.project.set_override(0, 1, 1)

    status, _ = _request(
        app,
        "POST",
        "/api/proposals/region-0001/expand/accept",
        {},
    )

    assert status == "200 OK"
    assert app.project.overrides == {(0, 0): 1, (0, 1): 1}


def test_proposal_accept_conflict_is_atomic_and_requires_confirmation(
    tmp_path,
):
    app = MosaicEditorApp(
        _save_project(tmp_path),
        refinement_report=_proposal_report(
            (0, 0, 0, 1),
            (0, 1, 0, 1),
        ),
    )
    app.project.set_override(0, 0, 0)

    status, payload = _request(
        app,
        "POST",
        "/api/proposals/region-0001/expand/accept",
        {},
    )

    assert status == "409 Conflict"
    assert payload["conflicts"][0]["tile_id"] == "placement-000000"
    assert app.project.overrides == {(0, 0): 0}
    assert app.dirty is False

    status, payload = _request(
        app,
        "POST",
        "/api/proposals/region-0001/expand/accept",
        {"confirm_conflicts": True},
    )

    assert status == "200 OK"
    assert app.project.overrides == {(0, 0): 1, (0, 1): 1}
    assert app.dirty is True
    assert payload["session"]["accepted"] == 1


def test_invalid_protected_proposal_accept_is_atomic(tmp_path):
    geometry = build_geometry(MosaicConfig(columns=2, rows=1), 2, 1)
    placements = list(geometry.placements)
    placements[1] = replace(
        placements[1], piece_type="edge_cut", piece_fraction=0.5
    )
    geometry = replace(geometry, placements=tuple(placements))
    app = MosaicEditorApp(
        _save_project(tmp_path, geometry),
        refinement_report=_proposal_report(
            (0, 0, 0, 1),
            (0, 1, 0, 1),
        ),
    )

    status, payload = _request(
        app,
        "POST",
        "/api/proposals/region-0001/expand/accept",
        {},
    )

    assert status == "400 Bad Request"
    assert "protected" in payload["error"]
    assert app.project.overrides == {}
    assert app.dirty is False


def test_reject_skip_and_reset_are_session_only(tmp_path):
    path = _save_project(tmp_path)
    before = path.read_bytes()
    app = MosaicEditorApp(
        path, refinement_report=_proposal_report((0, 0, 0, 1))
    )

    _, rejected = _request(
        app, "POST", "/api/proposals/region-0001/reject", {}
    )
    assert rejected["session"]["rejected"] == 1
    _, skipped = _request(
        app, "POST", "/api/proposals/region-0001/skip", {}
    )
    assert skipped["session"]["rejected"] == 0
    assert skipped["session"]["skipped"] == 1
    status, summary = _request(app, "GET", "/api/proposals/session")
    assert status == "200 OK"
    assert summary == skipped["session"]
    _, reset = _request(app, "POST", "/api/proposals/reset", {})
    assert reset["session"] == {
        "accepted": 0,
        "rejected": 0,
        "skipped": 0,
        "states": {},
    }
    assert path.read_bytes() == before
    assert app.project.overrides == {}
    assert app.dirty is False


def test_editor_loads_proposals_from_cache_without_source(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (120, 120), "black").save(source)
    config = MosaicConfig(
        tile_shape="hex",
        columns=3,
        rows=3,
        quantization_mode="bw",
    )
    geometry = build_geometry(config, 3, 3)
    project = MosaicProject.from_result(MosaicResult(
        columns=3,
        rows=3,
        grid=[[0, 0, 0], [0, 1, 0], [0, 0, 0]],
        palette=PALETTE,
        source_path=source,
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    cache_project_bw_evidence(project, source_analysis_width=120)
    path = project.save(tmp_path / "cached.json")
    source.unlink()
    app = MosaicEditorApp(path)

    status, payload = _request(app, "GET", "/api/proposals")

    assert status == "200 OK"
    assert "candidates" in payload


def test_contour_proposal_api_exposes_all_three_contours(tmp_path):
    app = MosaicEditorApp(
        _save_project(tmp_path),
        refinement_report=_proposal_report((0, 0, 0, 1)),
        contour_report=_contour_report(),
    )

    status, listing = _request(app, "GET", "/api/contour-proposals")
    detail_status, detail = _request(
        app, "GET", "/api/contour-proposals/contour-0001"
    )

    assert status == detail_status == "200 OK"
    assert listing["experiment"] == "continuous-contour-v1"
    assert detail["source_contour"]
    assert detail["current_mosaic_contour"]
    assert detail["alternatives"][0]["proposed_contour"]
    assert detail["recommendation"] == "source-trajectory"

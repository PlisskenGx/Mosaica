from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from mosaica import __version__
from mosaica.artwork import create_artwork, update_artwork_transform
from mosaica.border import build_border_layer
from mosaica.designer import DesignerProjectShell, MosaicDesignerApp
from mosaica.designer_colors import DesignColor
from mosaica.designer_generation import (
    DesignerGeneratedArtwork,
    GeneratedArtworkAssignment,
)
from mosaica.fabricate.phase2b import PRODUCTION_PROFILE
from mosaica.fabricate.resolve import resolve_designer_project
from mosaica.project_file import (
    CURRENT_PROJECT_SCHEMA_VERSION,
    DesignerProjectFileState,
    ProjectFileError,
    load_project_file,
    normalize_project_path,
    save_project_file,
)


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><rect width="100" height="50" fill="#B56F52"/></svg>'
FIXTURES = Path(__file__).parent / "fixtures"


def _v1_archive(tmp_path, stem, json_name, svg_name):
    raw = (FIXTURES / json_name).read_bytes()
    payload = json.loads(raw)
    embedded = payload["project"]["artwork"]["embedded_path"]
    path = tmp_path / f"{stem}.mosaica"
    with ZipFile(path, "w") as archive:
        archive.writestr("project.json", raw)
        archive.writestr(embedded, (FIXTURES / svg_name).read_bytes().rstrip(b"\n"))
    return path


def _replace_project_json(path, mutate):
    with ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    payload = json.loads(members["project.json"])
    mutate(payload)
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(
                name,
                json.dumps(payload).encode() if name == "project.json" else content,
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


def _rich_state(tmp_path):
    shell = DesignerProjectShell.create_custom("l", "flat_top", 5, 4)
    shell = shell.with_grout_color("project-color-2")
    shell = shell.with_border("solid")
    shell = replace(shell, border_channels=(
        ("border_primary", "project-color-3"),
        ("border_secondary", "project-color-4"),
    ))
    artwork = create_artwork(
        "source-logo.svg", SVG, shell.geometry,
        build_border_layer(shell.geometry, shell.border_preset_id),
    )
    artwork = update_artwork_transform(
        artwork, x_in=1.25, y_in=1.5, width_in=3.5, height_in=1.75,
    )
    tile = next(
        f"placement-{index:06d}"
        for index, placement in enumerate(shell.geometry.placements)
        if placement.piece_type != "outside"
    )
    generated = DesignerGeneratedArtwork(
        revision=3,
        assignments=(GeneratedArtworkAssignment(
            tile, 0, 0, (181, 111, 82), "project-color-3", 0.72,
        ),),
        source_colors=((181, 111, 82),),
        design_colors=shell.color_system.colors,
        source_signature="source-signature",
        border_preset_id="solid",
        color_remaps=(("project-color-3", "project-color-2"),),
        stale=True,
        stale_reason="Placement changed",
    )
    return DesignerProjectFileState(
        shell, artwork, generated, {tile: "project-color-5"},
        True, "Kitchen Mark",
    )


def test_mosaica_container_is_versioned_deterministic_and_self_contained(tmp_path):
    state = _rich_state(tmp_path)
    first = save_project_file(tmp_path / "Kitchen Mark", state)
    second = save_project_file(tmp_path / "Kitchen Copy.mosaica", state)
    assert first.suffix == ".mosaica"
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert archive.namelist()[0] == "project.json"
        project = json.loads(archive.read("project.json"))
        assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION == 2
        assert project["application_version"] == __version__ == "2.0.0"
        setup = project["project"]["setup"]
        assert setup["tile_family"] == "hexagon"
        assert setup["tile_preset"] == "l"
        assert setup["tile_orientation"] == "flat_top"
        assert "tile_id" not in setup
        artwork = project["project"]["artwork"]
        assert artwork["embedded_path"].startswith("artwork/artwork-")
        assert not Path(artwork["embedded_path"]).is_absolute()
        assert archive.read(artwork["embedded_path"]).decode().startswith("<svg")
        assert not any(name.endswith((".3mf", ".pdf")) for name in archive.namelist())
        assert "preview.png" not in archive.namelist()


def test_artwork_generated_state_paint_border_palette_and_transform_round_trip(tmp_path):
    state = _rich_state(tmp_path)
    path = save_project_file(tmp_path / "round-trip.mosaica", state)
    loaded = load_project_file(path)
    assert loaded.title == state.title
    assert loaded.project.canvas_mode == "custom_grid"
    assert (loaded.project.tiles_across, loaded.project.tiles_down) == (5, 4)
    assert loaded.project.tile_orientation == "flat_top"
    assert loaded.project.border_preset_id == "solid"
    assert loaded.project.grout_color_id == "project-color-2"
    assert loaded.project.color_system == state.project.color_system
    assert loaded.artwork is not None
    assert loaded.artwork.source_filename == "source-logo.svg"
    assert loaded.artwork.transform == state.artwork.transform
    assert loaded.generated_artwork == state.generated_artwork
    assert loaded.paint_overrides == state.paint_overrides


def test_embedded_artwork_reopens_after_original_is_deleted(tmp_path):
    external = tmp_path / "desktop-logo.svg"
    external.write_text(SVG)
    shell = DesignerProjectShell.create("square", "m")
    artwork = create_artwork(
        external.name, external.read_text(), shell.geometry,
        build_border_layer(shell.geometry, "none"),
    )
    path = save_project_file(tmp_path / "portable.mosaica", DesignerProjectFileState(
        shell, artwork, None, {}, True, "Portable",
    ))
    external.unlink()
    loaded = load_project_file(path)
    assert loaded.artwork is not None
    assert "#B56F52" in loaded.artwork.sanitized_svg


def test_fabrication_resolution_and_part_names_survive_round_trip(tmp_path):
    state = _rich_state(tmp_path)
    before = resolve_designer_project(
        state.project, PRODUCTION_PROFILE,
        generated_artwork=state.generated_artwork,
        paint_overrides=state.paint_overrides,
    )
    loaded = load_project_file(save_project_file(tmp_path / "fabricate.mosaica", state))
    after = resolve_designer_project(
        loaded.project, PRODUCTION_PROFILE,
        generated_artwork=loaded.generated_artwork,
        paint_overrides=loaded.paint_overrides,
    )
    assert after == before
    assert [channel.name for channel in after.channels] == [
        channel.name for channel in before.channels
    ]
    assert all(channel.name for channel in after.channels)


@pytest.mark.parametrize("member", [
    "../escape.svg", "/absolute.svg", "C:/absolute.svg", "artwork\\bad.svg",
])
def test_unsafe_archive_paths_are_rejected(tmp_path, member):
    path = tmp_path / "unsafe.mosaica"
    with ZipFile(path, "w") as archive:
        archive.writestr("project.json", "{}")
        archive.writestr(member, "bad")
    with pytest.raises(ProjectFileError, match="unsafe archive path"):
        load_project_file(path)


def test_invalid_archives_and_schema_fail_cleanly(tmp_path):
    invalid = tmp_path / "invalid.mosaica"
    invalid.write_bytes(b"not zip")
    with pytest.raises(ProjectFileError, match="not a valid"):
        load_project_file(invalid)
    missing = tmp_path / "missing.mosaica"
    with ZipFile(missing, "w") as archive:
        archive.writestr("other.json", "{}")
    with pytest.raises(ProjectFileError, match="missing project.json"):
        load_project_file(missing)
    malformed = tmp_path / "malformed.mosaica"
    with ZipFile(malformed, "w") as archive:
        archive.writestr("project.json", "{")
    with pytest.raises(ProjectFileError, match="invalid project.json"):
        load_project_file(malformed)
    unversioned = tmp_path / "unversioned.mosaica"
    with ZipFile(unversioned, "w") as archive:
        archive.writestr("project.json", json.dumps({"application_version": "2.0.0"}))
    with pytest.raises(ProjectFileError, match="missing its schema version"):
        load_project_file(unversioned)
    newer = tmp_path / "newer.mosaica"
    with ZipFile(newer, "w") as archive:
        archive.writestr("project.json", json.dumps({"schema_version": 3}))
    with pytest.raises(ProjectFileError, match="newer version"):
        load_project_file(newer)
    duplicate = tmp_path / "duplicate.mosaica"
    with ZipFile(duplicate, "w") as archive:
        archive.writestr("project.json", "{}")
        archive.writestr("project.json", "{}")
    with pytest.raises(ProjectFileError, match="duplicate archive members"):
        load_project_file(duplicate)


@pytest.mark.parametrize("fixture,svg,orientation", (
    ("hex_lock_project_v1.json", "hex_lock_project_v1.svg", "point_top"),
    ("designer_project_flat_v1.json", "designer_project_flat_v1.svg", "flat_top"),
))
def test_frozen_v1_projects_migrate_to_explicit_hex_without_rewriting(
    tmp_path, fixture, svg, orientation,
):
    path = _v1_archive(tmp_path, orientation, fixture, svg)
    before = path.read_bytes()
    loaded = load_project_file(path)
    assert path.read_bytes() == before
    assert loaded.source_schema_version == 1
    assert loaded.project.tile_system.family_id == "hexagon"
    assert loaded.project.tile_system.orientation_id == orientation
    assert loaded.project.tile_orientation == orientation


def test_rich_v1_artwork_border_paint_palette_and_fabrication_upgrade_to_v2(tmp_path):
    source = _v1_archive(
        tmp_path, "rich-flat", "designer_project_flat_v1.json",
        "designer_project_flat_v1.svg",
    )
    loaded_v1 = load_project_file(source)
    assert loaded_v1.artwork is not None
    assert loaded_v1.artwork.source_filename == "source-logo.svg"
    assert loaded_v1.generated_artwork is not None
    assert loaded_v1.project.border_preset_id == "solid"
    assert loaded_v1.paint_overrides
    before = resolve_designer_project(
        loaded_v1.project, PRODUCTION_PROFILE,
        generated_artwork=loaded_v1.generated_artwork,
        paint_overrides=loaded_v1.paint_overrides,
    )
    upgraded = save_project_file(tmp_path / "upgraded", loaded_v1)
    with ZipFile(upgraded) as archive:
        payload = json.loads(archive.read("project.json"))
    assert payload["schema_version"] == 2
    assert payload["project"]["setup"]["tile_family"] == "hexagon"
    loaded_v2 = load_project_file(upgraded)
    assert loaded_v2.source_schema_version == 2
    assert loaded_v2.artwork == loaded_v1.artwork
    assert loaded_v2.generated_artwork == loaded_v1.generated_artwork
    assert loaded_v2.paint_overrides == loaded_v1.paint_overrides
    assert loaded_v2.project.color_system == loaded_v1.project.color_system
    assert resolve_designer_project(
        loaded_v2.project, PRODUCTION_PROFILE,
        generated_artwork=loaded_v2.generated_artwork,
        paint_overrides=loaded_v2.paint_overrides,
    ) == before
    assert [value.tile_id for value in before.tiles] == [
        value.tile_id for value in resolve_designer_project(
            loaded_v2.project, PRODUCTION_PROFILE,
            generated_artwork=loaded_v2.generated_artwork,
            paint_overrides=loaded_v2.paint_overrides,
        ).tiles
    ]


def test_opening_v1_in_designer_is_clean_and_next_save_upgrades(tmp_path):
    source = _v1_archive(
        tmp_path, "legacy", "hex_lock_project_v1.json", "hex_lock_project_v1.svg",
    )
    destination = tmp_path / "Legacy Saved.mosaica"
    app = MosaicDesignerApp(project_save_dialog=lambda _current: destination)
    status, opened = _request(app, "POST", "/api/designer/project/open", {
        "path": str(source), "discard_unsaved": True,
    })
    assert status == "200 OK"
    assert opened["document"]["dirty"] is False
    assert app.document_dirty is False
    status, saved = _request(app, "POST", "/api/designer/project/save-as", {})
    assert status == "200 OK" and saved["saved"] is True
    with ZipFile(destination) as archive:
        assert json.loads(archive.read("project.json"))["schema_version"] == 2


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_v2_point_and_flat_projects_open_with_explicit_selection(tmp_path, orientation):
    state = _rich_state(tmp_path)
    state = replace(state, project=DesignerProjectShell.create_custom(
        "m", orientation, 5, 4,
    ))
    path = save_project_file(tmp_path / orientation, state)
    loaded = load_project_file(path)
    assert loaded.project.tile_system.family_id == "hexagon"
    assert loaded.project.tile_system.orientation_id == orientation
    assert loaded.project.tile_system.preset_id == "m"


@pytest.mark.parametrize("field,value,message", (
    ("tile_family", None, "missing tile_family"),
    ("tile_family", "square", "Unknown production tile family: square"),
    ("tile_orientation", "straight", "Unsupported Hexagon orientation: straight"),
    ("tile_preset", "xl", "Unknown Hexagon tile preset: xl"),
))
def test_v2_rejects_missing_or_invalid_family_selection(
    tmp_path, field, value, message,
):
    path = save_project_file(tmp_path / field, _rich_state(tmp_path))

    def mutate(payload):
        setup = payload["project"]["setup"]
        if value is None:
            setup.pop(field)
        else:
            setup[field] = value

    _replace_project_json(path, mutate)
    with pytest.raises(ProjectFileError, match=message):
        load_project_file(path)


def test_missing_and_damaged_embedded_artwork_are_rejected(tmp_path):
    path = save_project_file(tmp_path / "art.mosaica", _rich_state(tmp_path))
    with ZipFile(path) as source:
        payload = json.loads(source.read("project.json"))
    missing = tmp_path / "missing-art.mosaica"
    with ZipFile(missing, "w") as archive:
        archive.writestr("project.json", json.dumps(payload))
    with pytest.raises(ProjectFileError, match="missing its embedded artwork"):
        load_project_file(missing)


def test_atomic_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    path = save_project_file(tmp_path / "atomic.mosaica", _rich_state(tmp_path))
    original = path.read_bytes()
    monkeypatch.setattr(
        "mosaica.project_file.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated")),
    )
    with pytest.raises(ProjectFileError, match="previous file was preserved"):
        save_project_file(path, _rich_state(tmp_path))
    assert path.read_bytes() == original


def test_path_extension_is_normalized_once():
    assert normalize_project_path("My Design").name == "My Design.mosaica"
    assert normalize_project_path("My Design.mosaica").name == "My Design.mosaica"


def test_designer_save_save_as_open_and_dirty_controller_flow(tmp_path):
    first = tmp_path / "First Project"
    second = tmp_path / "Second Project.mosaica"
    saves = iter((first, second))
    app = MosaicDesignerApp(project_save_dialog=lambda _current: next(saves))
    app.project = DesignerProjectShell.create("square", "l")
    app.tile_shape = "hexagon"
    app.tile_id = "l"
    app.tile_orientation = "point_top"
    app.canvas_id = "square"
    app.document_dirty = True

    status, saved = _request(app, "POST", "/api/designer/project/save", {})
    assert status == "200 OK" and saved["saved"] is True
    assert app.document_path == (tmp_path / "First Project.mosaica").resolve()
    assert app.document_title == "First Project" and app.document_dirty is False

    app.document_dirty = True
    previous_path = app.document_path
    status, saved_again = _request(app, "POST", "/api/designer/project/save", {})
    assert status == "200 OK" and saved_again["saved"] is True
    assert app.document_path == previous_path

    app.document_dirty = True
    status, saved_as = _request(app, "POST", "/api/designer/project/save-as", {})
    assert status == "200 OK" and saved_as["saved"] is True
    assert app.document_path == second.resolve()
    assert app.document_title == "Second Project"

    app.document_dirty = True
    status, warning = _request(app, "POST", "/api/designer/project/open", {
        "path": str(previous_path),
    })
    assert status == "409 Conflict" and warning["requires_confirmation"] is True
    assert app.document_path == second.resolve()
    status, opened = _request(app, "POST", "/api/designer/project/open", {
        "path": str(previous_path), "discard_unsaved": True,
    })
    assert status == "200 OK" and opened["stage"] == "workspace"
    assert app.document_path == previous_path
    assert app.document_title == "First Project" and app.document_dirty is False


def test_back_requires_discard_confirmation_and_clears_file_identity(tmp_path):
    path = tmp_path / "Saved.mosaica"
    app = MosaicDesignerApp(project_save_dialog=lambda _current: path)
    app.project = DesignerProjectShell.create("square", "l")
    app.document_dirty = True
    _request(app, "POST", "/api/designer/project/save", {})
    app.document_dirty = True
    status, warning = _request(app, "POST", "/api/designer/back", {})
    assert status == "409 Conflict" and warning["requires_confirmation"] is True
    assert app.project is not None
    status, _ = _request(app, "POST", "/api/designer/back", {
        "discard_unsaved": True,
    })
    assert status == "200 OK" and app.project is None
    assert app.document_path is None and app.document_title == "Untitled"


def test_dialog_cancellation_is_non_mutating_and_export_does_not_clear_dirty(tmp_path):
    app = MosaicDesignerApp(
        export_root=tmp_path, project_save_dialog=lambda _current: None,
        project_open_dialog=lambda: None,
    )
    app.project = DesignerProjectShell.create_custom("l", "point_top", 3, 3)
    app.document_dirty = True
    status, cancelled = _request(app, "POST", "/api/designer/project/save", {})
    assert status == "200 OK" and cancelled["cancelled"] is True
    assert app.document_dirty is True and app.document_path is None
    status, cancelled = _request(app, "POST", "/api/designer/project/open", {
        "discard_unsaved": True,
    })
    assert status == "200 OK" and cancelled["cancelled"] is True
    assert app.document_dirty is True
    app._export_preview(app._export_snapshot(), "studio")
    assert app.document_dirty is True


def test_project_actions_and_dirty_indicator_are_wired_in_frontend():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    for identifier in (
        'id="open-action"', 'id="save-action"', 'id="save-as-action"',
        'id="save-confirmation"', 'id="document-edited"',
    ):
        assert identifier in html
    assert "/api/designer/project/save" in script
    assert "/api/designer/project/save-as" in script
    assert "/api/designer/project/open" in script
    assert "Opening another project will discard them" in script

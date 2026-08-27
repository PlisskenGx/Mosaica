from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import subprocess
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from . import __version__
from .artwork import ArtworkTransform, DesignerArtwork, sanitize_svg
from .designer_colors import DesignColor, DesignerColorResolution
from .designer_generation import (
    DesignerGeneratedArtwork,
    GeneratedArtworkAssignment,
)
from .tiles import get_tile_family


CURRENT_PROJECT_SCHEMA_VERSION = 2
SUPPORTED_PROJECT_SCHEMA_VERSIONS = (1, CURRENT_PROJECT_SCHEMA_VERSION)
# Compatibility export for callers that used the former constant name.
PROJECT_SCHEMA_VERSION = CURRENT_PROJECT_SCHEMA_VERSION
PROJECT_JSON = "project.json"
MAX_ARCHIVE_MEMBERS = 64
MAX_MEMBER_BYTES = 5_000_000
MAX_ARCHIVE_BYTES = 12_000_000
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ProjectFileError(ValueError):
    """A concise, user-safe project container validation error."""


@dataclass(frozen=True)
class DesignerProjectFileState:
    project: object
    artwork: DesignerArtwork | None
    generated_artwork: DesignerGeneratedArtwork | None
    paint_overrides: dict[str, str]
    artwork_edit_mode: bool
    title: str
    source_schema_version: int = CURRENT_PROJECT_SCHEMA_VERSION


def normalize_project_path(path: str | Path) -> Path:
    destination = Path(path).expanduser()
    if destination.suffix.lower() != ".mosaica":
        destination = destination.with_name(destination.name + ".mosaica")
    return destination


def _color_payload(color: DesignColor) -> dict:
    return color.to_dict()


def _generated_payload(generated: DesignerGeneratedArtwork | None) -> dict | None:
    if generated is None:
        return None
    return {
        "revision": generated.revision,
        "assignments": [value.to_dict() for value in generated.assignments],
        "source_colors": [list(value) for value in generated.source_colors],
        "design_colors": [_color_payload(value) for value in generated.design_colors],
        "source_signature": generated.source_signature,
        "border_preset_id": generated.border_preset_id,
        "color_remaps": [list(value) for value in generated.color_remaps],
        "coverage_threshold": generated.coverage_threshold,
        "samples_per_axis": generated.samples_per_axis,
        "stale": generated.stale,
        "stale_reason": generated.stale_reason,
    }


def _project_payload(state: DesignerProjectFileState) -> tuple[dict, dict[str, bytes]]:
    project = state.project
    artwork_payload = None
    assets: dict[str, bytes] = {}
    if state.artwork is not None:
        content = state.artwork.sanitized_svg.encode("utf-8")
        digest = sha256(content).hexdigest()
        asset_id = f"artwork-{digest[:16]}"
        embedded_path = f"artwork/{asset_id}.svg"
        assets[embedded_path] = content
        artwork_payload = {
            "asset_id": asset_id,
            "embedded_path": embedded_path,
            "sha256": digest,
            "original_filename": state.artwork.source_filename,
            "media_type": "image/svg+xml",
            "source_view_box": list(state.artwork.source_view_box),
            "source_aspect_ratio": state.artwork.source_aspect_ratio,
            "transform": state.artwork.transform.to_dict(),
            "initial_transform": state.artwork.initial_transform.to_dict(),
            "selected": state.artwork.selected,
        }
    payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "application_version": __version__,
        "project": {
            "title": state.title,
            "setup": {
                "canvas_id": project.canvas.id,
                "canvas_mode": project.canvas_mode,
                "canvas_width_in": project.canvas.width_in,
                "canvas_height_in": project.canvas.height_in,
                "tile_family": project.tile_system.family_id,
                "tile_preset": project.tile_system.preset_id,
                "tile_orientation": project.tile_orientation,
                "tiles_across": project.tiles_across,
                "tiles_down": project.tiles_down,
            },
            "colors": {
                "items": [_color_payload(value) for value in project.color_system.colors],
                "role_to_color_id": dict(sorted(
                    project.color_system.role_to_color_id.items()
                )),
            },
            "grout_color_id": project.grout_color_id,
            "border": {
                "preset_id": project.border_preset_id,
                "channels": [list(value) for value in project.border_channels],
            },
            "artwork": artwork_payload,
            "generated_artwork": _generated_payload(state.generated_artwork),
            "paint_overrides": dict(sorted(state.paint_overrides.items())),
            "artwork_edit_mode": bool(state.artwork_edit_mode),
        },
    }
    return payload, assets


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _write_archive(path: Path, payload: dict, assets: dict[str, bytes]) -> None:
    project_json = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    with ZipFile(path, "w") as archive:
        archive.writestr(_zip_info(PROJECT_JSON), project_json)
        for name in sorted(assets):
            archive.writestr(_zip_info(name), assets[name])


def save_project_file(
    path: str | Path,
    state: DesignerProjectFileState,
) -> Path:
    destination = normalize_project_path(path)
    payload, assets = _project_payload(state)
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(raw_path)
        _write_archive(temporary, payload, assets)
        load_project_file(temporary)
        os.replace(temporary, destination)
        temporary = None
        return destination
    except PermissionError as exc:
        raise ProjectFileError(
            "Mosaica cannot save to that location. Check folder permissions."
        ) from exc
    except OSError as exc:
        raise ProjectFileError(
            "Mosaica could not safely replace the project file. The previous file was preserved."
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_members(archive: ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    names = [value.filename for value in infos]
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ProjectFileError("This project contains too many archive members.")
    if len(names) != len(set(names)):
        raise ProjectFileError("This project contains duplicate archive members.")
    total = 0
    result = {}
    for info in infos:
        name = info.filename
        pure = PurePosixPath(name)
        if (
            not name or name.startswith(("/", "\\")) or "\\" in name
            or pure.is_absolute() or PureWindowsPath(name).is_absolute()
            or ".." in pure.parts
        ):
            raise ProjectFileError("This project contains an unsafe archive path.")
        if info.file_size > MAX_MEMBER_BYTES:
            raise ProjectFileError("This project contains an unreasonably large member.")
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise ProjectFileError("This project is unreasonably large.")
        if not info.is_dir():
            result[name] = archive.read(info)
    return result


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProjectFileError(f"Malformed project state: {label} must be an object.")
    return value


def _transform(value, label: str) -> ArtworkTransform:
    data = _object(value, label)
    try:
        transform = ArtworkTransform(**{
            key: float(data[key])
            for key in ("x_in", "y_in", "width_in", "height_in")
        })
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectFileError(f"Malformed project state: invalid {label}.") from exc
    if transform.width_in <= 0 or transform.height_in <= 0:
        raise ProjectFileError(f"Malformed project state: invalid {label} size.")
    return transform


def _load_colors(value) -> DesignerColorResolution:
    data = _object(value, "colors")
    try:
        colors = tuple(DesignColor(**_object(item, "color")) for item in data["items"])
        roles = {str(key): str(item) for key, item in data["role_to_color_id"].items()}
        return DesignerColorResolution(colors, roles)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProjectFileError):
            raise
        raise ProjectFileError("Malformed project color state.") from exc


def _load_generated(value) -> DesignerGeneratedArtwork | None:
    if value is None:
        return None
    data = _object(value, "generated artwork")
    try:
        assignments = tuple(
            GeneratedArtworkAssignment(
                tile_id=str(item["tile_id"]), row=int(item["row"]),
                column=int(item["column"]),
                source_rgb=tuple(int(channel) for channel in item["source_rgb"]),
                color_id=str(item["color_id"]), coverage=float(item["coverage"]),
            )
            for item in data["assignments"]
        )
        return DesignerGeneratedArtwork(
            revision=int(data["revision"]), assignments=assignments,
            source_colors=tuple(tuple(int(channel) for channel in rgb)
                                for rgb in data["source_colors"]),
            design_colors=tuple(DesignColor(**item) for item in data["design_colors"]),
            source_signature=str(data["source_signature"]),
            border_preset_id=str(data["border_preset_id"]),
            color_remaps=tuple(tuple(str(part) for part in item)
                               for item in data.get("color_remaps", [])),
            coverage_threshold=float(data["coverage_threshold"]),
            samples_per_axis=int(data["samples_per_axis"]),
            stale=bool(data.get("stale", False)),
            stale_reason=data.get("stale_reason"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectFileError("Malformed generated artwork state.") from exc


def _migrate_v1_to_v2(payload: dict) -> dict:
    migrated = deepcopy(payload)
    setup = _object(
        _object(migrated.get("project"), "project").get("setup"), "setup",
    )
    setup["tile_family"] = "hexagon"
    if "tile_id" in setup:
        setup["tile_preset"] = setup.pop("tile_id")
    migrated["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    return migrated


def _migrate_to_current(payload: dict) -> tuple[dict, int]:
    if "schema_version" not in payload:
        raise ProjectFileError("This project is missing its schema version.")
    version = payload["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ProjectFileError(f"Unsupported Mosaica project schema version: {version}.")
    if version > CURRENT_PROJECT_SCHEMA_VERSION:
        raise ProjectFileError(
            "This project was created by a newer version of Mosaica and cannot be opened by this version."
        )
    if version not in SUPPORTED_PROJECT_SCHEMA_VERSIONS:
        raise ProjectFileError(f"Unsupported Mosaica project schema version: {version}.")
    if version == 1:
        return _migrate_v1_to_v2(payload), version
    return payload, version


def _load_state(payload: dict, members: dict[str, bytes]) -> DesignerProjectFileState:
    payload, source_schema_version = _migrate_to_current(payload)
    if not isinstance(payload.get("application_version"), str):
        raise ProjectFileError("This project is missing its application version.")
    data = _object(payload.get("project"), "project")
    setup = _object(data.get("setup"), "setup")
    try:
        tile_family = str(setup["tile_family"])
        tile_preset = str(setup["tile_preset"])
        tile_orientation = str(setup["tile_orientation"])
    except KeyError as exc:
        raise ProjectFileError(
            f"Malformed project state: setup is missing {exc.args[0]}."
        ) from exc
    try:
        family = get_tile_family(tile_family)
        selection = family.selection(tile_orientation, tile_preset)
    except ValueError as exc:
        raise ProjectFileError(f"Invalid project tile system: {exc}") from exc
    try:
        from .designer import DesignerProjectShell
        if setup["canvas_mode"] == "custom_grid":
            project = DesignerProjectShell.create_custom(
                selection.preset_id, selection.orientation_id,
                int(setup["tiles_across"]), int(setup["tiles_down"]),
                family_id=selection.family_id,
            )
        else:
            project = DesignerProjectShell.create(
                str(setup["canvas_id"]), selection.preset_id,
                selection.orientation_id, family_id=selection.family_id,
            )
        if (
            abs(project.canvas.width_in - float(setup["canvas_width_in"])) > 1e-8
            or abs(project.canvas.height_in - float(setup["canvas_height_in"])) > 1e-8
        ):
            raise ProjectFileError("Saved canvas dimensions do not match its setup state.")
        colors = _load_colors(data["colors"])
        project = project.with_color_system(colors)
        project = project.with_grout_color(str(data["grout_color_id"]))
        border = _object(data["border"], "border")
        project = project.with_border(str(border["preset_id"]))
        channels = tuple((str(item[0]), str(item[1])) for item in border["channels"])
        for _, color_id in channels:
            colors.by_id(color_id)
        project = replace(project, border_channels=channels)
    except ProjectFileError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectFileError("Malformed Designer project setup state.") from exc

    artwork = None
    artwork_data = data.get("artwork")
    if artwork_data is not None:
        artwork_data = _object(artwork_data, "artwork")
        embedded = str(artwork_data.get("embedded_path", ""))
        if embedded not in members:
            raise ProjectFileError("This project is missing its embedded artwork asset.")
        content = members[embedded]
        if sha256(content).hexdigest() != artwork_data.get("sha256"):
            raise ProjectFileError("The embedded artwork asset is damaged.")
        try:
            source = content.decode("utf-8")
            sanitized, view_box = sanitize_svg(
                str(artwork_data["original_filename"]), source,
            )
            artwork = DesignerArtwork(
                source_filename=str(artwork_data["original_filename"]),
                sanitized_svg=sanitized,
                source_view_box=view_box,
                source_aspect_ratio=float(artwork_data["source_aspect_ratio"]),
                transform=_transform(artwork_data["transform"], "artwork transform"),
                initial_transform=_transform(
                    artwork_data["initial_transform"], "initial artwork transform",
                ),
                selected=bool(artwork_data.get("selected", False)),
            )
        except (KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, ProjectFileError):
                raise
            raise ProjectFileError("The embedded SVG artwork is invalid.") from exc

    generated = _load_generated(data.get("generated_artwork"))
    overrides = data.get("paint_overrides", {})
    if not isinstance(overrides, dict):
        raise ProjectFileError("Malformed manual paint override state.")
    overrides = {str(key): str(value) for key, value in overrides.items()}
    valid_tiles = {
        f"placement-{index:06d}"
        for index, placement in enumerate(project.geometry.placements)
        if placement.piece_type != "outside"
    }
    if not set(overrides).issubset(valid_tiles):
        raise ProjectFileError("Manual paint overrides reference unknown tiles.")
    for color_id in overrides.values():
        project.color_system.by_id(color_id)
    if generated is not None:
        if not {item.tile_id for item in generated.assignments}.issubset(valid_tiles):
            raise ProjectFileError("Generated artwork references unknown tiles.")
    title = str(data.get("title") or "Untitled").strip() or "Untitled"
    return DesignerProjectFileState(
        project, artwork, generated, overrides,
        bool(data.get("artwork_edit_mode", artwork is not None)), title,
        source_schema_version,
    )


def load_project_file(path: str | Path) -> DesignerProjectFileState:
    source = Path(path).expanduser()
    try:
        with ZipFile(source, "r") as archive:
            members = _safe_members(archive)
    except (BadZipFile, EOFError) as exc:
        raise ProjectFileError("This is not a valid Mosaica project file.") from exc
    except FileNotFoundError as exc:
        raise ProjectFileError("The selected Mosaica project could not be found.") from exc
    except PermissionError as exc:
        raise ProjectFileError("Mosaica does not have permission to open this project.") from exc
    if PROJECT_JSON not in members:
        raise ProjectFileError("This project is missing project.json.")
    try:
        payload = json.loads(members[PROJECT_JSON].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectFileError("This project contains invalid project.json data.") from exc
    return _load_state(_object(payload, PROJECT_JSON), members)


def _tk_dialog(*, save: bool, current: Path | None = None) -> Path | None:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        if save:
            selected = filedialog.asksaveasfilename(
                title="Save Mosaica Project", defaultextension=".mosaica",
                filetypes=(("Mosaica Project", "*.mosaica"),),
                initialfile=(current.name if current else "Untitled Mosaic.mosaica"),
            )
        else:
            selected = filedialog.askopenfilename(
                title="Open Mosaica Project",
                filetypes=(("Mosaica Project", "*.mosaica"),),
            )
    finally:
        root.destroy()
    return Path(selected) if selected else None


def choose_save_project_path(current: Path | None = None) -> Path | None:
    if platform.system() != "Darwin":
        return _tk_dialog(save=True, current=current)
    default_name = current.name if current else "Untitled Mosaic.mosaica"
    script = (
        'POSIX path of (choose file name with prompt "Save Mosaica Project" '
        f'default name {json.dumps(default_name)})'
    )
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def choose_open_project_path() -> Path | None:
    if platform.system() != "Darwin":
        return _tk_dialog(save=False)
    result = subprocess.run(
        ["osascript", "-e", 'POSIX path of (choose file with prompt "Open Mosaica Project")'],
        capture_output=True, text=True,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None

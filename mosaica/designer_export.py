from __future__ import annotations

from dataclasses import dataclass
import logging
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from typing import TYPE_CHECKING, Callable

from .designer_generation import DesignerGeneratedArtwork
from .fabricate.modes import FabricationMode, resolve_fabrication_mode

if TYPE_CHECKING:
    from .fabricate.panelize import PanelizationPlan


_EXPORT_LOG = logging.getLogger("mosaica.designer.export")


@dataclass(frozen=True)
class DesignerExportSnapshot:
    project: object
    generated_artwork: DesignerGeneratedArtwork | None
    paint_overrides: dict[str, str]
    document_title: str


@dataclass(frozen=True)
class DesignerExportResult:
    output_directory: Path
    mode: FabricationMode
    panel_count: int
    three_mf_count: int
    print_guide_path: Path
    manifest_path: Path
    three_mf_paths: tuple[Path, ...]

    def to_dict(self) -> dict:
        return {
            "output_directory": str(self.output_directory),
            "mode": self.mode.value,
            "mode_display_name": resolve_fabrication_mode(self.mode).display_name,
            "panel_count": self.panel_count,
            "three_mf_count": self.three_mf_count,
            "print_guide": self.print_guide_path.name,
            "manifest": self.manifest_path.name,
            "three_mf_files": [path.name for path in self.three_mf_paths],
        }


@dataclass(frozen=True)
class DesignerStlExportResult:
    output_directory: Path
    mode: FabricationMode
    panel_count: int
    stl_paths: tuple[Path, ...]
    manifest_path: Path
    geometry_signature: str

    def to_dict(self) -> dict:
        return {
            "output_directory": str(self.output_directory),
            "kind": "stl",
            "mode": self.mode.value,
            "mode_display_name": resolve_fabrication_mode(self.mode).display_name,
            "panel_count": self.panel_count,
            "stl_count": len(self.stl_paths),
            "stl_files": [
                str(path.relative_to(self.output_directory)) for path in self.stl_paths
            ],
            "manifest": self.manifest_path.name,
            "geometry_signature": self.geometry_signature,
        }


def sanitize_export_name(document_title: str) -> str:
    title = document_title.strip()
    if not title or title.lower() == "untitled":
        title = "Project"
    title = re.sub(r"[^A-Za-z0-9._ -]+", "", title)
    title = re.sub(r"[ ._-]+", "_", title).strip("_")
    return title or "Project"


def _stl_part_token(name: str) -> str:
    value = name.replace(" - ", " ")
    value = re.sub(r"[^A-Za-z0-9 -]+", "", value)
    return re.sub(r" +", "_", value.strip()) or "Part"


def choose_flat_export_path(format_name: str, default_name: str) -> Path | None:
    extension = ".jpg" if format_name.lower() == "jpeg" else f".{format_name.lower()}"
    filename = Path(default_name).with_suffix(extension).name
    if platform.system() == "Darwin":
        script = (
            'POSIX path of (choose file name with prompt "Export Mosaica Design" '
            f'default name {__import__("json").dumps(filename)})'
        )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True,
        )
        return Path(result.stdout.strip()) if result.returncode == 0 else None
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.asksaveasfilename(
            title="Export Mosaica Design", defaultextension=extension,
            filetypes=((format_name.upper(), f"*{extension}"),),
            initialfile=filename,
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None


def panelization_summary(plan: PanelizationPlan) -> dict:
    model = plan.model
    mode = resolve_fabrication_mode(plan.fabrication_mode)
    tile_channels = tuple(
        channel for channel in model.channels if channel.kind == "tile_color"
    )
    return {
        "mode": {
            "id": mode.mode_id,
            "display_name": mode.display_name,
            "recommended": mode.mode is FabricationMode.STUDIO,
        },
        "panel_count": len(plan.panels),
        "rows": plan.rows,
        "columns": plan.columns,
        "finished_mosaic": {
            "width_mm": model.artwork_width_mm,
            "height_mm": model.artwork_height_mm,
            "width_in": model.artwork_width_mm / 25.4,
            "height_in": model.artwork_height_mm / 25.4,
        },
        "tile": {
            "preset_id": model.tile_preset_id,
            "flat_to_flat_mm": model.tile_flat_to_flat_mm,
            "orientation": model.tile_orientation,
        },
        "safe_envelope_mm": {
            "width": plan.safe_envelope_mm[0],
            "height": plan.safe_envelope_mm[1],
        },
        "palette_color_count": len(tile_channels),
        "palette": [
            {
                "channel_id": channel.channel_id,
                "name": channel.name,
                "display_color": channel.display_color or "#808080",
            }
            for channel in tile_channels
        ],
        "panels": [
            {
                "panel_id": panel.panel_id,
                "row": panel.row,
                "column": panel.column,
                "bounds_mm": list(panel.bounds_mm),
                "width_mm": panel.width_mm,
                "height_mm": panel.height_mm,
            }
            for panel in plan.panels
        ],
    }


class DesignerFabricationExportService:
    """Bridge authoritative in-memory Designer state to Fabricate."""

    def __init__(
        self,
        output_root: str | Path | None = None,
        *,
        folder_opener: Callable[[Path], None] | None = None,
    ) -> None:
        self.output_root = (
            Path(output_root)
            if output_root is not None
            else Path.home() / "Downloads"
        )
        self._folder_opener = folder_opener or self._open_folder_native

    @staticmethod
    def _model(snapshot: DesignerExportSnapshot):
        from .fabricate.phase2b import PRODUCTION_PROFILE
        from .fabricate.resolve import resolve_designer_project

        return resolve_designer_project(
            snapshot.project,
            PRODUCTION_PROFILE,
            generated_artwork=snapshot.generated_artwork,
            paint_overrides=dict(snapshot.paint_overrides),
        )

    def preview(
        self,
        snapshot: DesignerExportSnapshot,
        mode: FabricationMode | str,
    ) -> dict:
        from .fabricate.panelize import panelize_model

        definition = resolve_fabrication_mode(mode)
        plan = panelize_model(self._model(snapshot), mode=definition.mode)
        return panelization_summary(plan)

    def allocate_output_directory(self, document_title: str) -> Path:
        return self.allocate_named_output_directory(document_title)

    def allocate_named_output_directory(
        self, document_title: str, suffix: str | None = None,
    ) -> Path:
        self.output_root.mkdir(parents=True, exist_ok=True)
        stem = f"Mosaica_{sanitize_export_name(document_title)}"
        if suffix:
            stem += f"_{sanitize_export_name(suffix)}"
        for suffix in range(1, 10_000):
            name = stem if suffix == 1 else f"{stem}_{suffix}"
            candidate = self.output_root / name
            try:
                candidate.mkdir()
                return candidate
            except FileExistsError:
                continue
        raise OSError("Mosaica could not allocate a unique export folder.")

    def generate(
        self,
        snapshot: DesignerExportSnapshot,
        mode: FabricationMode | str,
        output_directory: str | Path,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> DesignerExportResult:
        from .fabricate.three_mf import export_three_mf_package

        definition = resolve_fabrication_mode(mode)
        if progress is not None:
            progress({
                "phase": "resolving",
                "current_panel": None,
                "completed_panels": 0,
                "total_panels": 0,
                "panel_index": None,
                "message": "Resolving your design…",
            })
        model = self._model(snapshot)
        package = export_three_mf_package(
            model,
            output_directory,
            mode=definition.mode,
            project_name=(
                "Mosaica Project"
                if snapshot.document_title.strip().lower() in {"", "untitled"}
                else snapshot.document_title.strip()
            ),
            progress=progress,
        )
        return DesignerExportResult(
            output_directory=package.output_directory,
            mode=definition.mode,
            panel_count=len(package.three_mf_paths),
            three_mf_count=len(package.three_mf_paths),
            print_guide_path=package.print_guide_path,
            manifest_path=package.manifest_path,
            three_mf_paths=package.three_mf_paths,
        )

    def generate_stl(
        self,
        snapshot: DesignerExportSnapshot,
        mode: FabricationMode | str,
        output_directory: str | Path,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> DesignerStlExportResult:
        from .fabricate.panelize import generate_panelization_package, panelize_model
        from .fabricate.three_mf import _part_identity

        definition = resolve_fabrication_mode(mode)
        if progress is not None:
            progress({
                "phase": "resolving", "current_panel": None,
                "completed_panels": 0, "total_panels": 0, "panel_index": None,
                "message": "Resolving your design…",
            })
        model = self._model(snapshot)
        plan = panelize_model(model, mode=definition.mode)
        if progress is not None:
            progress({
                "phase": "building_panels", "current_panel": None,
                "completed_panels": 0, "total_panels": len(plan.panels),
                "panel_index": None, "message": "Building STL panel bodies…",
            })
        package = generate_panelization_package(
            model, output_directory, mode=definition.mode,
        )
        manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
        records = {
            record["filename"]: record
            for record in manifest["body_channel_ownership"]
        }
        named_paths = []
        for path in package.stl_paths:
            relative = str(path.relative_to(package.output_directory))
            record = records[relative]
            identity = _part_identity(model.channel(record["channel"]))
            filename = (
                f"Panel_{record['panel_id']}_"
                f"{_stl_part_token(str(identity['user_facing_name']))}.stl"
            )
            named_path = path.with_name(filename)
            path.rename(named_path)
            record["filename"] = str(named_path.relative_to(package.output_directory))
            record["user_facing_name"] = identity["user_facing_name"]
            record["project_palette_index"] = identity["project_palette_index"]
            record["project_color_name"] = identity["project_color_name"]
            record["project_color_value"] = identity["project_color_value"]
            named_paths.append(named_path)
        package.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        return DesignerStlExportResult(
            output_directory=package.output_directory,
            mode=definition.mode,
            panel_count=len(plan.panels),
            stl_paths=tuple(named_paths),
            manifest_path=package.manifest_path,
            geometry_signature=package.geometry_signature,
        )

    def open_folder(self, output_directory: str | Path) -> None:
        path = Path(output_directory).resolve()
        root = self.output_root.resolve()
        if path != root and root not in path.parents:
            raise ValueError("Mosaica can only open a folder created by this export session.")
        if not path.is_dir():
            raise ValueError("The export folder is no longer available.")
        self._folder_opener(path)

    @staticmethod
    def _open_folder_native(path: Path) -> None:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

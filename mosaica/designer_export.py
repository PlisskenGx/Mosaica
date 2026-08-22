from __future__ import annotations

from dataclasses import dataclass
import logging
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
            "panel_count": self.panel_count,
            "three_mf_count": self.three_mf_count,
            "print_guide": self.print_guide_path.name,
            "manifest": self.manifest_path.name,
            "three_mf_files": [path.name for path in self.three_mf_paths],
        }


def sanitize_export_name(document_title: str) -> str:
    title = document_title.strip()
    if not title or title.lower() == "untitled":
        title = "Project"
    title = re.sub(r"[^A-Za-z0-9._ -]+", "", title)
    title = re.sub(r"[ ._-]+", "_", title).strip("_")
    return title or "Project"


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
            "recommended": mode.mode is FabricationMode.FAST,
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
        self.output_root.mkdir(parents=True, exist_ok=True)
        stem = f"Mosaica_{sanitize_export_name(document_title)}"
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
    ) -> DesignerExportResult:
        from .fabricate.three_mf import export_three_mf_package

        definition = resolve_fabrication_mode(mode)
        package = export_three_mf_package(
            self._model(snapshot),
            output_directory,
            mode=definition.mode,
            project_name=(
                "Mosaica Project"
                if snapshot.document_title.strip().lower() in {"", "untitled"}
                else snapshot.document_title.strip()
            ),
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

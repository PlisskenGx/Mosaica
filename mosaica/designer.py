from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from importlib.resources import files
import json
import logging
from math import ceil, sqrt
from pathlib import Path
import shutil
from socketserver import ThreadingMixIn
from threading import RLock, Thread
from urllib.parse import parse_qs
import webbrowser
from wsgiref.handlers import SimpleHandler
from wsgiref.simple_server import (
    WSGIRequestHandler,
    WSGIServer,
    make_server,
)

from . import __version__

from .geometry import GridGeometry, vertex_constrained_panel_dimensions
from .model import MosaicConfig
from .border import (
    BORDER_PRESETS,
    build_border_layer,
    border_preset,
)
from .builtin_artwork import builtin_artwork
from .designer_colors import (
    CURATED_MOSAICA_PALETTE, DEFAULT_DESIGNER_COLORS, LEGACY_PAINT_SLOTS,
    DesignerColorResolution,
)
from .artwork import (
    DesignerArtwork,
    create_artwork,
    reset_artwork,
    update_artwork_transform,
)
from .designer_generation import (
    DesignerGeneratedArtwork,
    generate_designer_artwork,
    mark_generated_stale,
)
from .designer_export import (
    DesignerExportSnapshot, choose_flat_export_path, sanitize_export_name,
    DesignerFabricationExportService,
)
from .designer_flat_export import export_flat_design
from .project_file import (
    DesignerProjectFileState,
    choose_open_project_path,
    choose_save_project_path,
    load_project_file,
    normalize_project_path,
    save_project_file,
)
from .tiles import (
    DEFAULT_TILE_FAMILY_ID, HEXAGON_PRESETS, TileSizePreset,
    TileSystemSelection, get_tile_family, production_tile_families,
    resolve_tile_system,
)


_KEYBOARD_DIRECTIONS = {
    "ArrowLeft": (-1.0, 0.0),
    "ArrowRight": (1.0, 0.0),
    "ArrowUp": (0.0, -1.0),
    "ArrowDown": (0.0, 1.0),
}


def tile_keyboard_navigation(
    tiles: list[dict],
) -> tuple[dict[str, dict[str, str]], str | None]:
    """Return cached cardinal navigation derived from physical centers."""

    points = tuple(
        (tile["id"], float(tile["center_in"][0]), float(tile["center_in"][1]))
        for tile in tiles if tile.get("editable")
    )
    navigation, center_id = _cached_tile_keyboard_navigation(points)
    return {
        tile_id: dict(directions) for tile_id, directions in navigation
    }, center_id


@lru_cache(maxsize=32)
def _cached_tile_keyboard_navigation(
    points: tuple[tuple[str, float, float], ...],
) -> tuple[tuple[tuple[str, tuple[tuple[str, str], ...]], ...], str | None]:
    if not points:
        return (), None
    center_x = sum(point[1] for point in points) / len(points)
    center_y = sum(point[2] for point in points) / len(points)
    center_id = min(
        points,
        key=lambda point: (
            (point[1] - center_x) ** 2 + (point[2] - center_y) ** 2,
            point[0],
        ),
    )[0]
    navigation: dict[str, dict[str, str]] = {}
    for tile_id, x, y in points:
        offsets = []
        for candidate_id, candidate_x, candidate_y in points:
            if candidate_id == tile_id:
                continue
            dx = candidate_x - x
            dy = candidate_y - y
            distance = sqrt(dx * dx + dy * dy)
            if distance > 1e-9:
                offsets.append((candidate_id, dx, dy, distance))
        if not offsets:
            navigation[tile_id] = {}
            continue
        nearest = min(value[3] for value in offsets)
        nearby = [value for value in offsets if value[3] <= nearest * 1.35]
        neighbors = {}
        for key, (direction_x, direction_y) in _KEYBOARD_DIRECTIONS.items():
            candidates = []
            for candidate_id, dx, dy, distance in nearby:
                alignment = (dx * direction_x + dy * direction_y) / distance
                if alignment <= 0.25:
                    continue
                candidates.append((-alignment, distance, candidate_id))
            if candidates:
                neighbors[key] = min(candidates)[2]
        navigation[tile_id] = neighbors
    return tuple(
        (tile_id, tuple(sorted(directions.items())))
        for tile_id, directions in navigation.items()
    ), center_id


MM_PER_INCH = 25.4
DESIGNER_GROUT_MM = 1.8
P1S_BUILD_AREA_MM = 256.0
CUSTOM_GRID_MAX = 200
CANVAS_PREVIEW_REM_PER_INCH = 0.20
_TRANSPORT_LOG = logging.getLogger("mosaica.designer.transport")
_EXPORT_LOG = logging.getLogger("mosaica.designer.export")
if not _TRANSPORT_LOG.handlers:
    _transport_handler = logging.StreamHandler()
    _transport_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    _TRANSPORT_LOG.addHandler(_transport_handler)
_TRANSPORT_LOG.setLevel(logging.INFO)


def estimate_minimum_print_plates(
    width_in: float,
    height_in: float,
    build_area_mm: float = P1S_BUILD_AREA_MM,
) -> dict:
    """Return a rectangular segmentation lower bound, not a packing result."""

    if width_in <= 0 or height_in <= 0 or build_area_mm <= 0:
        raise ValueError("Canvas and build-area dimensions must be positive.")
    columns = ceil(width_in * MM_PER_INCH / build_area_mm)
    rows = ceil(height_in * MM_PER_INCH / build_area_mm)
    return {
        "build_area_mm": build_area_mm,
        "columns": columns,
        "rows": rows,
        "estimated_minimum_plates": columns * rows,
        "method": "rectangular lower bound",
    }


@dataclass(frozen=True)
class CanvasPreset:
    id: str
    name: str
    width_in: float
    height_in: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "width_in": self.width_in,
            "height_in": self.height_in,
            "aspect_ratio": self.width_in / self.height_in,
            # One shared linear scale makes both aspect and physical size
            # comparable across every setup card.
            "preview_width_rem": round(
                self.width_in * CANVAS_PREVIEW_REM_PER_INCH, 4
            ),
            "preview_height_rem": round(
                self.height_in * CANVAS_PREVIEW_REM_PER_INCH, 4
            ),
        }


TilePreset = TileSizePreset


CANVAS_PRESETS = (
    CanvasPreset("square", "Square", 24.0, 24.0),
    CanvasPreset("portrait", "Portrait", 24.0, 36.0),
    CanvasPreset("landscape", "Landscape", 36.0, 24.0),
)

TILE_PRESETS = HEXAGON_PRESETS

_CANVASES = {value.id: value for value in CANVAS_PRESETS}
# Read-only construction compatibility for integrations that still reopen the
# removed preset by ID. It is intentionally absent from setup/API choices.
_LEGACY_CANVASES = {
    "square-s": CanvasPreset("square-s", "Small Square", 24.0, 24.0),
    "square-m": CanvasPreset("square-m", "Medium Square", 36.0, 36.0),
    "square-l": CanvasPreset("square-l", "Large Square", 48.0, 48.0),
    "wide": CanvasPreset("wide", "Wide", 60.0, 30.0),
    "panoramic": CanvasPreset("panoramic", "Panoramic", 72.0, 30.0),
}
_TILES = {value.id: value for value in TILE_PRESETS}


@dataclass(frozen=True)
class DesignerProjectShell:
    canvas: CanvasPreset
    tile: TilePreset
    grout_mm: float
    geometry: GridGeometry
    tile_system: TileSystemSelection
    color_system: DesignerColorResolution = DEFAULT_DESIGNER_COLORS
    grout_color_id: str = "project-color-1"
    border_channels: tuple[tuple[str, str], ...] = (
        ("border_primary", "project-color-2"),
        ("border_secondary", "project-color-3"),
    )
    border_preset_id: str = "none"
    canvas_mode: str = "preset"
    tiles_across: int | None = None
    tiles_down: int | None = None

    def __post_init__(self) -> None:
        _, selection = resolve_tile_system(self.tile_system)
        if selection.preset_id != self.tile.id:
            raise ValueError("Tile-system preset does not match the project tile.")
        if selection.orientation_id != self.geometry.orientation:
            raise ValueError("Tile-system orientation does not match project geometry.")

    @property
    def tile_family(self) -> str:
        return self.tile_system.family_id

    @property
    def tile_preset_id(self) -> str:
        return self.tile_system.preset_id

    @property
    def tile_orientation(self) -> str:
        return self.tile_system.orientation_id

    @classmethod
    def create(
        cls, canvas_id: str, tile_id: str, orientation: str = "point_top",
        *, family_id: str = DEFAULT_TILE_FAMILY_ID,
    ) -> DesignerProjectShell:
        try:
            canvas = {**_CANVASES, **_LEGACY_CANVASES}[canvas_id]
        except KeyError as exc:
            raise ValueError(f"Unknown canvas preset: {canvas_id}") from exc
        family = get_tile_family(family_id)
        selection = family.selection(orientation, tile_id)
        tile = family.preset(selection.preset_id)
        geometry = family.build_preset_panel(
            selection.preset_id, selection.orientation_id, DESIGNER_GROUT_MM,
            canvas.width_in, canvas.height_in,
        )
        return cls(canvas, tile, DESIGNER_GROUT_MM, geometry, selection)

    @classmethod
    def create_custom(
        cls, tile_id: str, orientation: str, tiles_across: int, tiles_down: int,
        *, family_id: str = DEFAULT_TILE_FAMILY_ID,
    ) -> DesignerProjectShell:
        for name, value in (("Tiles Across", tiles_across), ("Tiles Down", tiles_down)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a whole number.")
            if not 1 <= value <= CUSTOM_GRID_MAX:
                raise ValueError(f"{name} must be between 1 and {CUSTOM_GRID_MAX}.")
        family = get_tile_family(family_id)
        selection = family.selection(orientation, tile_id)
        tile = family.preset(selection.preset_id)
        geometry = family.build_custom_grid(
            selection.preset_id, selection.orientation_id, DESIGNER_GROUT_MM,
            tiles_across, tiles_down,
        )
        canvas = CanvasPreset(
            "custom", "Custom", geometry.width_in, geometry.height_in,
        )
        return cls(
            canvas, tile, DESIGNER_GROUT_MM, geometry, selection,
            canvas_mode="custom_grid", tiles_across=tiles_across,
            tiles_down=tiles_down,
        )

    @classmethod
    def create_physical(
        cls, tile_id: str, orientation: str, width_in: float, height_in: float,
        *, family_id: str = DEFAULT_TILE_FAMILY_ID,
    ) -> DesignerProjectShell:
        for name, value in (("Canvas width", width_in), ("Canvas height", height_in)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be positive.")
        family = get_tile_family(family_id)
        selection = family.selection(orientation, tile_id)
        tile = family.preset(selection.preset_id)
        geometry = family.build_preset_panel(
            selection.preset_id, selection.orientation_id, DESIGNER_GROUT_MM,
            float(width_in), float(height_in),
        )
        canvas = CanvasPreset("custom", "Custom", float(width_in), float(height_in))
        return cls(
            canvas, tile, DESIGNER_GROUT_MM, geometry, selection,
            canvas_mode="custom_physical",
        )

    def with_border(self, preset_id: str) -> DesignerProjectShell:
        border_preset(preset_id)
        family = get_tile_family(self.tile_family)
        if preset_id not in family.supported_border_presets():
            raise ValueError(
                f"Border preset {preset_id!r} is unsupported for {family.display_name}."
            )
        return replace(self, border_preset_id=preset_id)

    def with_color_system(
        self, color_system: DesignerColorResolution,
    ) -> DesignerProjectShell:
        return replace(self, color_system=color_system)

    def with_grout_color(self, color_id: str) -> DesignerProjectShell:
        self.color_system.by_id(color_id)
        return replace(self, grout_color_id=color_id)

    def with_border_channel(
        self, channel_id: str, color_id: str,
    ) -> DesignerProjectShell:
        preset = border_preset(self.border_preset_id)
        if channel_id not in set(preset.pattern_roles):
            raise ValueError(f"Unknown channel for this Border: {channel_id}")
        self.color_system.by_id(color_id)
        channels = dict(self.border_channels)
        channels[channel_id] = color_id
        return replace(self, border_channels=tuple(channels.items()))

    def to_dict(
        self,
        generated_artwork: DesignerGeneratedArtwork | None = None,
        paint_overrides: dict[str, str] | None = None,
    ) -> dict:
        paint_overrides = {
            tile_id: dict(LEGACY_PAINT_SLOTS).get(color_id, color_id)
            for tile_id, color_id in (paint_overrides or {}).items()
        }
        visible = tuple(
            value for value in self.geometry.placements
            if value.piece_type != "outside"
        )
        full_count = sum(value.piece_type == "full" for value in visible)
        border = build_border_layer(self.geometry, self.border_preset_id)
        assignments = {
            value.tile_id: value
            for value in border.assignments
        }
        available = set(border.available_artwork_placement_ids)
        effective_roles = {
            f"placement-{index:06d}": (
                assignments[f"placement-{index:06d}"].color_role
                if f"placement-{index:06d}" in assignments
                else "background"
            )
            for index, _ in enumerate(self.geometry.placements)
        }
        generated_assignments = {
            value.tile_id: value
            for value in generated_artwork.assignments
        } if generated_artwork is not None else {}
        border_color_ids = dict(self.border_channels)
        lower_color_ids = {
            tile_id: (
                generated_artwork.remapped_color_id(
                    generated_assignments[tile_id].color_id
                )
                if tile_id in generated_assignments and tile_id in available
                else border_color_ids.get(
                    role, self.color_system.resolve(role).color_id,
                )
            )
            for tile_id, role in effective_roles.items()
        }
        effective_color_ids = {
            tile_id: (
                paint_overrides[tile_id]
                if tile_id in paint_overrides
                else lower_color_ids[tile_id]
            )
            for tile_id in effective_roles
        }
        effective_resolution = self.color_system
        color_counts = effective_resolution.count_visible_color_ids(
            (
                placement.piece_type,
                effective_color_ids[f"placement-{index:06d}"],
            )
            for index, placement in enumerate(self.geometry.placements)
        )
        navigation, center_tile_id = tile_keyboard_navigation([
            {
                "id": f"placement-{index:06d}",
                "center_in": [placement.center_x_in, placement.center_y_in],
                "editable": True,
            }
            for index, placement in enumerate(self.geometry.placements)
            if placement.piece_type != "outside"
        ])
        return {
            "canvas_preset": self.canvas.to_dict(),
            "canvas_mode": self.canvas_mode,
            "custom_grid": (
                {"tiles_across": self.tiles_across, "tiles_down": self.tiles_down}
                if self.canvas_mode == "custom_grid" else None
            ),
            "tile_preset": self.tile.to_dict(),
            "tile_family": self.tile_family,
            "tile_shape": self.tile_family,
            "tile_orientation": self.tile_orientation,
            "grout_mm": self.grout_mm,
            "color_system": effective_resolution.to_dict(),
            "color_counts": [value.to_dict() for value in color_counts],
            "grout": {
                "color_id": self.grout_color_id,
                "display_color": effective_resolution.by_id(
                    self.grout_color_id
                ).display_color,
                "width_mm": self.grout_mm,
            },
            "paint": {
                "overrides": dict(sorted(paint_overrides.items())),
                "override_count": len(paint_overrides),
                "curated_palette": [
                    {
                        "color_id": effective_resolution.color_id_for_rgb(
                            tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
                        ),
                        "name": name,
                        "display_color": color,
                    }
                    for name, color in CURATED_MOSAICA_PALETTE
                ],
            },
            "border": {
                **border.to_dict(),
                "channel_mappings": [
                    {
                        "channel_id": role,
                        "color_id": color_id,
                        "display_color": self.color_system.by_id(color_id).display_color,
                    }
                    for role, color_id in self.border_channels
                ],
                "color_channels": [
                    {
                        "channel_id": role,
                        "color_id": border_color_ids[role],
                        "display_color": self.color_system.by_id(
                            border_color_ids[role]
                        ).display_color,
                    }
                    for role in (
                        border_preset(self.border_preset_id).pattern_roles
                        if self.border_preset_id != "none" else ()
                    )
                ],
            },
            "generated_artwork": (
                {
                    **generated_artwork.to_dict(),
                    "display_active": True,
                }
                if generated_artwork is not None else None
            ),
            "print_plate_estimate": estimate_minimum_print_plates(
                self.geometry.width_in,
                self.geometry.height_in,
            ),
            "geometry": {
                "shape": self.geometry.shape,
                "orientation": self.tile_orientation,
                "target_width_in": self.canvas.width_in,
                "target_height_in": self.canvas.height_in,
                "width_in": self.geometry.width_in,
                "height_in": self.geometry.height_in,
                "columns": self.geometry.columns,
                "rows": self.geometry.rows,
                "full_tile_count": full_count,
                "clipped_piece_count": len(visible) - full_count,
                "visible_piece_count": len(visible),
                "outside_placements_excluded": sum(
                    value.piece_type == "outside"
                    for value in self.geometry.placements
                ),
                "keyboard_navigation": navigation,
                "keyboard_center_tile_id": center_tile_id,
                "tiles": [
                    {
                        "id": f"placement-{index:06d}",
                        "row": placement.row,
                        "column": placement.column,
                        "piece_type": placement.piece_type,
                        "piece_fraction": placement.piece_fraction,
                        "principal_grid": placement.principal_grid,
                        "principal_row": placement.principal_row,
                        "principal_column": placement.principal_column,
                        "center_in": [
                            placement.center_x_in, placement.center_y_in,
                        ],
                        "vertices_in": [list(point) for point in placement.vertices_in],
                        "full_vertices_in": (
                            [list(point) for point in placement.full_vertices_in]
                            if placement.piece_type != "full"
                            or placement.principal_grid else None
                        ),
                        "parent_center_in": (
                            [placement.center_x_in, placement.center_y_in]
                            if placement.piece_type != "full" else None
                        ),
                        "border_owned": f"placement-{index:06d}" in assignments,
                        "protected": f"placement-{index:06d}" in assignments,
                        "artwork_available": f"placement-{index:06d}" in available,
                        "color_role": effective_roles[f"placement-{index:06d}"],
                        "color_id": effective_color_ids[f"placement-{index:06d}"],
                        "display_color": next(
                            color.display_color for color in effective_resolution.colors
                            if color.color_id == effective_color_ids[
                                f"placement-{index:06d}"
                            ]
                        ),
                        "lower_color_id": lower_color_ids[
                            f"placement-{index:06d}"
                        ],
                        "lower_display_color": next(
                            color.display_color for color in effective_resolution.colors
                            if color.color_id == lower_color_ids[
                                f"placement-{index:06d}"
                            ]
                        ),
                        "generated_artwork": (
                            f"placement-{index:06d}" in generated_assignments
                            and f"placement-{index:06d}" in available
                        ),
                        "manual_override": paint_overrides.get(
                            f"placement-{index:06d}"
                        ),
                        "editable": True,
                    }
                    for index, placement in enumerate(self.geometry.placements)
                    if placement.piece_type != "outside"
                ],
            },
        }


DESIGNER_ASSETS = {
    "/": ("designer.html", "text/html; charset=utf-8"),
    "/designer.css": ("designer.css", "text/css; charset=utf-8"),
    "/designer.js": ("designer.js", "text/javascript; charset=utf-8"),
}


class MosaicDesignerApp:
    """Product-facing preset flow backed by the physical geometry engine."""

    def __init__(
        self,
        *,
        export_root: str | Path | None = None,
        export_folder_opener=None,
        project_open_dialog=None,
        project_save_dialog=None,
        export_file_dialog=None,
    ) -> None:
        self._state_lock = RLock()
        self.tile_shape: str | None = None
        self.tile_id: str | None = None
        self.tile_orientation: str | None = None
        self.canvas_id: str | None = None
        self._canvas_confirmed = False
        self._canvas_first_setup = False
        self._setup_stage: str | None = None
        self._custom_width_in = 24.0
        self._custom_height_in = 18.0
        self.project: DesignerProjectShell | None = None
        self.artwork: DesignerArtwork | None = None
        self.generated_artwork: DesignerGeneratedArtwork | None = None
        self.paint_overrides: dict[str, str] = {}
        self.artwork_edit_mode = True
        self.document_title = "Untitled"
        self.document_dirty = False
        self.document_path: Path | None = None
        self.welcome_active = True
        self._project_open_dialog = project_open_dialog or choose_open_project_path
        self._project_save_dialog = project_save_dialog or choose_save_project_path
        self._export_file_dialog = export_file_dialog or choose_flat_export_path
        self._flat_export_paths: set[Path] = set()
        self.export_service = DesignerFabricationExportService(
            export_root, folder_opener=export_folder_opener,
        )
        self._export_jobs: dict[str, dict] = {}
        self._next_export_job = 1
        self._active_export_job_id: str | None = None

    def _reset_for_new_mosaic(self, *, canvas_first: bool) -> None:
        """Clear document-scoped state at the authoritative New Mosaic boundary."""
        self.tile_shape = None
        self.tile_id = None
        self.tile_orientation = None
        self.canvas_id = None
        self._canvas_confirmed = False
        self._canvas_first_setup = canvas_first
        self._setup_stage = "canvas" if canvas_first else "shape"
        self._custom_width_in = 24.0
        self._custom_height_in = 18.0
        self.project = None
        self.artwork = None
        self.generated_artwork = None
        self.paint_overrides = {}
        self.artwork_edit_mode = True
        self.document_title = "Untitled"
        self.document_dirty = False
        self.document_path = None
        self.welcome_active = False

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path.startswith("/api/designer"):
            with self._state_lock:
                return self._dispatch(environ, start_response)
        return self._dispatch(environ, start_response)

    def _dispatch(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path in DESIGNER_ASSETS:
                return self._asset(path, start_response)
            if method == "GET" and path == "/api/designer":
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/new":
                body = self._request_json(environ)
                if (
                    self.project is not None and self.document_dirty
                    and not body.get("discard_unsaved", False)
                ):
                    return self._json(start_response, "409 Conflict", {
                        "error": (
                            "You have unsaved changes. Starting a new mosaic "
                            "will discard them."
                        ),
                        "requires_confirmation": True,
                    })
                self._reset_for_new_mosaic(
                    canvas_first=bool(body.get("canvas_first", False)),
                )
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path in {
                "/api/designer/project/save",
                "/api/designer/project/save-as",
            }:
                body = self._request_json(environ)
                saved = self._save_project_document(
                    body, save_as=path.endswith("save-as"),
                )
                return self._json(start_response, "200 OK", saved)
            if method == "POST" and path == "/api/designer/project/open":
                body = self._request_json(environ)
                if self.document_dirty and not body.get("discard_unsaved", False):
                    return self._json(start_response, "409 Conflict", {
                        "error": (
                            "You have unsaved changes. Opening another project "
                            "will discard them."
                        ),
                        "requires_confirmation": True,
                    })
                opened = self._open_project_document(body)
                return self._json(start_response, "200 OK", opened)
            if method == "POST" and path == "/api/designer/shape":
                self.welcome_active = False
                body = self._request_json(environ)
                shape = body.get("shape")
                family = get_tile_family(shape)
                self.tile_shape = family.id
                self.tile_id = None
                default_orientation = family.orientations()[0].id
                self.tile_orientation = family.normalize_orientation(
                    body.get("orientation", default_orientation)
                )
                self._setup_stage = "tile"
                self.project = None
                self.artwork = None
                self.generated_artwork = None
                self.paint_overrides = {}
                self.document_dirty = False
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/tile":
                if self.tile_shape is None:
                    raise ValueError("Select a tile shape before configuring tiles.")
                body = self._request_json(environ)
                tile_id = body.get("tile_id")
                family = get_tile_family(self.tile_shape)
                default_orientation = family.orientations()[0].id
                selection = family.selection(
                    body.get("orientation", self.tile_orientation or default_orientation),
                    tile_id,
                )
                self.tile_id = selection.preset_id
                self.tile_orientation = selection.orientation_id
                # A remembered canvas is setup continuity, not confirmation.
                # The product flow resumes at Canvas; the guarded branch only
                # preserves the legacy API's explicit Canvas-then-Tile order.
                self.project = (
                    DesignerProjectShell.create_physical(
                        selection.preset_id, selection.orientation_id,
                        self._custom_width_in, self._custom_height_in,
                        family_id=selection.family_id,
                    )
                    if self._canvas_confirmed and self.canvas_id == "custom"
                    else DesignerProjectShell.create(
                        self.canvas_id, selection.preset_id,
                        selection.orientation_id, family_id=selection.family_id,
                    )
                    if self._canvas_confirmed
                    and self.canvas_id in {**_CANVASES, **_LEGACY_CANVASES}
                    else None
                )
                self._setup_stage = None if self.project is not None else "canvas"
                self._canvas_confirmed = False
                self.artwork = None
                self.generated_artwork = None
                self.paint_overrides = {}
                self.document_dirty = False
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/canvas-preview":
                return self._json(
                    start_response, "200 OK", self._canvas_preview_payload(
                        self._request_json(environ),
                    ),
                )
            if method == "POST" and path == "/api/designer/canvas":
                if self.tile_id is None or self.tile_orientation is None:
                    body = self._request_json(environ)
                    canvas_id = body.get("canvas_id")
                    if canvas_id == "custom":
                        if body.get("width_in") is None:
                            raise ValueError(
                                "Configure the tile system before selecting a custom canvas."
                            )
                        self._custom_width_in = float(body.get("width_in"))
                        self._custom_height_in = float(body.get("height_in"))
                        if self._custom_width_in <= 0 or self._custom_height_in <= 0:
                            raise ValueError("Custom canvas dimensions must be positive.")
                    elif canvas_id not in {**_CANVASES, **_LEGACY_CANVASES}:
                        raise ValueError(f"Unknown canvas preset: {canvas_id}")
                    self.canvas_id = canvas_id
                    self._canvas_confirmed = True
                    self._setup_stage = "shape"
                    if self.tile_shape is None and not self._canvas_first_setup:
                        self.tile_shape = "hexagon"
                        self.tile_orientation = "point_top"
                    self.project = None
                    self.artwork = None
                    self.generated_artwork = None
                    self.paint_overrides = {}
                    self.document_dirty = False
                    return self._json(start_response, "200 OK", self.payload())
                body = self._request_json(environ)
                canvas_id = body.get("canvas_id")
                if canvas_id == "custom":
                    if body.get("width_in") is not None:
                        self.project = DesignerProjectShell.create_physical(
                            self.tile_id, self.tile_orientation,
                            float(body.get("width_in")), float(body.get("height_in")),
                            family_id=self.tile_shape,
                        )
                    else:
                        self.project = DesignerProjectShell.create_custom(
                            self.tile_id, self.tile_orientation,
                            body.get("tiles_across"), body.get("tiles_down"),
                            family_id=self.tile_shape,
                        )
                elif canvas_id in {**_CANVASES, **_LEGACY_CANVASES}:
                    self.project = DesignerProjectShell.create(
                        canvas_id, self.tile_id, self.tile_orientation,
                        family_id=self.tile_shape,
                    )
                else:
                    raise ValueError(f"Unknown canvas preset: {canvas_id}")
                self.canvas_id = canvas_id
                self._setup_stage = None
                self._canvas_confirmed = False
                self.artwork = None
                self.generated_artwork = None
                self.paint_overrides = {}
                self.document_dirty = False
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/border":
                if self.project is None:
                    raise ValueError("Create a Designer project before selecting a border.")
                previous_project = self.project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                preset_id = self._request_json(environ).get("preset_id")
                changed = preset_id != self.project.border_preset_id
                self.project = self.project.with_border(preset_id)
                if changed:
                    self.generated_artwork = mark_generated_stale(
                        self.generated_artwork, "Border changed",
                    )
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/grout":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                color_id = self._request_json(environ).get("color_id")
                updated = project.with_grout_color(color_id)
                changed = updated != project
                self.project = updated
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/border/color":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                body = self._request_json(environ)
                color_id = body.get("color_id")
                project.color_system.by_id(color_id)
                updated = project.with_border_channel(
                    body.get("channel_id"), color_id,
                )
                changed = updated != project
                self.project = updated
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/artwork/color":
                project = self._require_project()
                if self.generated_artwork is None:
                    raise ValueError("Generate Artwork before remapping its colors.")
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                body = self._request_json(environ)
                updated = self.generated_artwork.with_channel_color(
                    body.get("channel_id"), body.get("color_id"),
                )
                changed = updated != self.generated_artwork
                self.generated_artwork = updated
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/colors/add":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                body = self._request_json(environ)
                updated = project.color_system.add_color(
                    body.get("display_color"), body.get("name"),
                )
                self.project = project.with_color_system(updated)
                if self.generated_artwork is not None:
                    self.generated_artwork = replace(
                        self.generated_artwork, design_colors=updated.colors,
                    )
                self.document_dirty = True
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/colors/update":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                body = self._request_json(environ)
                color_id = body.get("color_id")
                updated = project.color_system.update_color(
                    color_id, display_color=body.get("display_color"),
                    name=body.get("name"),
                )
                changed = updated != project.color_system
                self.project = project.with_color_system(updated)
                if changed and self.generated_artwork is not None:
                    self.generated_artwork = replace(
                        self.generated_artwork, design_colors=updated.colors,
                    )
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/colors/remove":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                color_id = self._request_json(environ).get("color_id")
                visible_count = next((
                    value["count"] for value in previous_project["color_counts"]
                    if value["color_id"] == color_id
                ), 0)
                referenced_by_generation = self.generated_artwork is not None and any(
                    value.color_id == color_id
                    for value in self.generated_artwork.assignments
                )
                referenced_by_paint = color_id in self.paint_overrides.values()
                if visible_count or referenced_by_generation or referenced_by_paint:
                    raise ValueError(
                        f"This color is used by {visible_count} visible pieces and cannot be removed."
                    )
                updated = project.color_system.remove_color(color_id)
                self.project = project.with_color_system(updated)
                if self.generated_artwork is not None:
                    self.generated_artwork = replace(
                        self.generated_artwork, design_colors=updated.colors,
                    )
                self.document_dirty = True
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/paint":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                body = self._request_json(environ)
                placement_ids = body.get("placement_ids")
                mode = body.get("mode", "paint")
                if not isinstance(placement_ids, list) or not all(
                    isinstance(value, str) for value in placement_ids
                ):
                    raise ValueError("Tiles requires a list of placement IDs.")
                if mode != "paint":
                    raise ValueError("Tiles only accepts direct color assignments.")
                unique_ids = tuple(dict.fromkeys(placement_ids))
                border = build_border_layer(
                    project.geometry, project.border_preset_id,
                )
                visible = {
                    f"placement-{index:06d}"
                    for index, placement in enumerate(project.geometry.placements)
                    if placement.piece_type != "outside"
                }
                invalid = [value for value in unique_ids if value not in visible]
                if invalid:
                    raise ValueError(
                        "Tile edits must target visible physical placements: "
                        + ", ".join(invalid)
                    )
                color_id = body.get("color_id")
                if color_id is None and body.get("slot_id") is not None:
                    color_id = dict(LEGACY_PAINT_SLOTS).get(body.get("slot_id"))
                project.color_system.by_id(color_id)
                updated = dict(self.paint_overrides)
                updated.update({value: color_id for value in unique_ids})
                changed = updated != self.paint_overrides
                self.paint_overrides = updated
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/paint/clear":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                changed = bool(self.paint_overrides)
                self.paint_overrides = {}
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/paint/erase":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                placement_ids = self._request_json(environ).get("placement_ids")
                if not isinstance(placement_ids, list) or not all(
                    isinstance(value, str) for value in placement_ids
                ):
                    raise ValueError("Tiles requires a list of placement IDs.")
                unique_ids = tuple(dict.fromkeys(placement_ids))
                visible = {
                    f"placement-{index:06d}"
                    for index, placement in enumerate(project.geometry.placements)
                    if placement.piece_type != "outside"
                }
                invalid = [value for value in unique_ids if value not in visible]
                if invalid:
                    raise ValueError(
                        "Tile edits must target visible physical placements: "
                        + ", ".join(invalid)
                    )
                updated = {
                    tile_id: color_id
                    for tile_id, color_id in self.paint_overrides.items()
                    if tile_id not in unique_ids
                }
                changed = updated != self.paint_overrides
                self.paint_overrides = updated
                self.document_dirty = self.document_dirty or changed
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path in {
                "/api/designer/artwork/upload",
                "/api/designer/artwork/replace",
            }:
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                had_generated = self.generated_artwork is not None
                if path.endswith("/replace"):
                    self._require_artwork()
                body = self._request_json(environ)
                filename = body.get("filename")
                svg_content = body.get("svg_content")
                if not isinstance(filename, str) or not isinstance(svg_content, str):
                    raise ValueError("SVG upload requires filename and svg_content strings.")
                border = build_border_layer(
                    project.geometry, project.border_preset_id,
                )
                self.artwork = create_artwork(
                    filename, svg_content, project.geometry, border,
                )
                self.generated_artwork = None
                self.artwork_edit_mode = True
                self.document_dirty = True
                if not path.endswith("/replace") and not had_generated:
                    return self._json(
                        start_response, "200 OK", self._artwork_state_payload(),
                    )
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/artwork/builtin":
                project = self._require_project()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                had_generated = self.generated_artwork is not None
                body = self._request_json(environ)
                shape = builtin_artwork(body.get("shape_id"))
                border = build_border_layer(
                    project.geometry, project.border_preset_id,
                )
                self.artwork = create_artwork(
                    shape.filename, shape.svg, project.geometry, border,
                )
                self.generated_artwork = None
                self.artwork_edit_mode = True
                self.document_dirty = True
                if not had_generated:
                    return self._json(
                        start_response, "200 OK", self._artwork_state_payload(),
                    )
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/artwork/transform":
                self._require_artwork()
                body = self._request_json(environ)
                updated = update_artwork_transform(
                    self.artwork,
                    x_in=body.get("x_in"),
                    y_in=body.get("y_in"),
                    width_in=body.get("width_in"),
                    height_in=body.get("height_in"),
                )
                if updated.transform != self.artwork.transform:
                    self.generated_artwork = mark_generated_stale(
                        self.generated_artwork, "Artwork placement changed",
                    )
                self.artwork = updated
                self.artwork_edit_mode = True
                self.document_dirty = True
                return self._json(
                    start_response, "200 OK", self._artwork_state_payload(),
                )
            if method == "POST" and path == "/api/designer/artwork/selection":
                artwork = self._require_artwork()
                selected = self._request_json(environ).get("selected")
                if not isinstance(selected, bool):
                    raise ValueError("Artwork selected state must be boolean.")
                self.artwork = replace(artwork, selected=selected)
                return self._json(
                    start_response, "200 OK", self._artwork_state_payload(),
                )
            if method == "POST" and path == "/api/designer/artwork/reset":
                project = self._require_project()
                artwork = self._require_artwork()
                border = build_border_layer(
                    project.geometry, project.border_preset_id,
                )
                self.artwork = reset_artwork(artwork, project.geometry, border)
                self.generated_artwork = mark_generated_stale(
                    self.generated_artwork, "Artwork placement reset",
                )
                self.artwork_edit_mode = True
                self.document_dirty = True
                return self._json(
                    start_response, "200 OK", self._artwork_state_payload(),
                )
            if method == "POST" and path == "/api/designer/artwork/remove":
                self._require_artwork()
                previous_project = self._require_project().to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                self.artwork = None
                self.generated_artwork = None
                self.artwork_edit_mode = True
                self.document_dirty = True
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/artwork/edit":
                artwork = self._require_artwork()
                self.artwork = replace(artwork, selected=True)
                self.artwork_edit_mode = True
                return self._json(
                    start_response, "200 OK", self._artwork_state_payload(),
                )
            if method == "POST" and path == "/api/designer/artwork/generate":
                project = self._require_project()
                artwork = self._require_artwork()
                previous_project = project.to_dict(
                    self.generated_artwork, self.paint_overrides,
                )
                border = build_border_layer(
                    project.geometry, project.border_preset_id,
                )
                revision = (
                    self.generated_artwork.revision + 1
                    if self.generated_artwork is not None else 1
                )
                # Compute completely before replacing session state so every
                # validation/rendering failure is atomic.
                generated = generate_designer_artwork(
                    artwork, project.geometry, border,
                    project.color_system, revision,
                )
                if self.generated_artwork is not None:
                    previous_remaps = dict(self.generated_artwork.color_remaps)
                    generated_color_ids = {
                        assignment.color_id for assignment in generated.assignments
                    }
                    generated = replace(
                        generated,
                        color_remaps=tuple(sorted(
                            (source_id, target_id)
                            for source_id, target_id in previous_remaps.items()
                            if source_id in generated_color_ids
                        )),
                    )
                self.project = project.with_color_system(
                    DesignerColorResolution(
                        generated.design_colors,
                        dict(project.color_system.role_to_color_id),
                    )
                )
                self.generated_artwork = generated
                self.artwork = replace(artwork, selected=False)
                self.artwork_edit_mode = False
                self.document_dirty = True
                return self._json(
                    start_response, "200 OK",
                    self._design_state_payload(previous_project),
                )
            if method == "POST" and path == "/api/designer/export/preview":
                body = self._request_json(environ)
                summary = self._export_preview(
                    self._export_snapshot(), body.get("mode", "studio"),
                )
                return self._json(start_response, "200 OK", summary)
            if method == "POST" and path == "/api/designer/export/start":
                body = self._request_json(environ)
                job = self._start_export_job(
                    body.get("mode", "studio"), body.get("kind", "print_package"),
                )
                return self._json(start_response, "202 Accepted", job)
            if method == "POST" and path == "/api/designer/export/file":
                body = self._request_json(environ)
                format_name = str(body.get("format", "")).lower()
                if format_name not in {"svg", "png", "jpg", "jpeg"}:
                    raise ValueError("Choose SVG, PNG, or JPG export.")
                default_name = (
                    f"{sanitize_export_name(self.document_title or 'Untitled Mosaic')}"
                    f".{format_name}"
                )
                requested = body.get("path")
                destination = (
                    Path(requested) if isinstance(requested, str) and requested
                    else self._export_file_dialog(format_name, default_name)
                )
                if destination is None:
                    return self._json(start_response, "200 OK", {
                        "cancelled": True, "format": format_name,
                    })
                result = export_flat_design(
                    self._export_snapshot(), destination, format_name,
                )
                resolved = result.path.resolve()
                self._flat_export_paths.add(resolved)
                return self._json(start_response, "200 OK", result.to_dict())
            if method == "POST" and path == "/api/designer/export/file/open":
                requested = self._request_json(environ).get("path")
                target = Path(requested).resolve() if isinstance(requested, str) else None
                if target not in self._flat_export_paths or not target.is_file():
                    raise ValueError("Choose a file exported during this Mosaica session.")
                self.export_service._folder_opener(target.parent)
                return self._json(start_response, "200 OK", {"opened": True})
            if method == "GET" and path == "/api/designer/export/status":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                job_id = (query.get("id") or [None])[0]
                return self._json(
                    start_response, "200 OK", self._export_job(job_id),
                )
            if method == "POST" and path == "/api/designer/export/open":
                body = self._request_json(environ)
                job_id = body.get("id", body.get("job_id"))
                job = self._export_job(job_id)
                if job["status"] != "complete":
                    raise ValueError("Complete the export before opening its folder.")
                self.export_service.open_folder(job["result"]["output_directory"])
                return self._json(
                    start_response, "200 OK",
                    {"opened": True, "output_directory": job["result"]["output_directory"]},
                )
            if method == "POST" and path == "/api/designer/back":
                body = self._request_json(environ)
                if (
                    self.project is not None and self.document_dirty
                    and not body.get("discard_unsaved", False)
                ):
                    return self._json(start_response, "409 Conflict", {
                        "error": (
                            "You have unsaved changes. Returning to setup will "
                            "discard them."
                        ),
                        "requires_confirmation": True,
                    })
                if self.project is not None:
                    self.welcome_active = False
                    self.project = None
                    self.artwork = None
                    self.generated_artwork = None
                    self.paint_overrides = {}
                    self.artwork_edit_mode = True
                    self.document_dirty = False
                    self.document_path = None
                    self.document_title = "Untitled"
                    self._canvas_confirmed = False
                    self._setup_stage = "canvas"
                elif self._setup_stage == "canvas":
                    self._setup_stage = "tile"
                elif self._setup_stage == "tile":
                    self._setup_stage = "shape"
                return self._json(start_response, "200 OK", self.payload())
            return self._json(start_response, "404 Not Found", {"error": "Not found."})
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            return self._json(start_response, "400 Bad Request", {"error": str(exc)})

    def payload(self) -> dict:
        project_payload = (
            self.project.to_dict(self.generated_artwork, self.paint_overrides)
            if self.project is not None else None
        )
        if project_payload is not None:
            project_payload["artwork"] = (
                {
                    **self.artwork.to_dict(),
                    "edit_mode": self.artwork_edit_mode,
                }
                if self.artwork is not None else None
            )
        canvas_presets = [value.to_dict() for value in CANVAS_PRESETS]
        if (
            self.tile_shape == "hexagon"
            and self.tile_id is not None and self.tile_orientation is not None
        ):
            tile = get_tile_family("hexagon").preset(self.tile_id)
            config = MosaicConfig(
                tile_shape="hex", tile_width_in=tile.flat_to_flat_in,
                grout_width_in=DESIGNER_GROUT_MM / MM_PER_INCH,
                hex_orientation=self.tile_orientation,
            )
            for preset in canvas_presets:
                width, height = vertex_constrained_panel_dimensions(
                    config, preset["width_in"], preset["height_in"],
                )
                preset["actual"] = {
                    "width_in": width, "height_in": height,
                }
        return {
            "app_version": __version__,
            "stage": (
                "workspace" if self.project is not None
                else "welcome" if self.welcome_active
                else self._setup_stage if self._setup_stage is not None
                else "canvas" if self._canvas_first_setup and self.canvas_id is None
                else "canvas" if self.tile_id is not None
                else "tile" if self.tile_shape is not None
                else "shape"
            ),
            "canvas_presets": canvas_presets,
            "tile_families": [
                {
                    "id": family.id, "display_name": family.display_name,
                    "orientations": [
                        {"id": value.id, "display_name": value.display_name}
                        for value in family.orientations()
                    ],
                }
                for family in production_tile_families()
            ],
            "tile_presets": [
                value.to_dict()
                for value in (
                    get_tile_family(self.tile_shape).presets()
                    if self.tile_shape else TILE_PRESETS
                )
            ],
            "border_presets": [
                value.to_dict() for value in BORDER_PRESETS
                if (
                    self.project is None
                    or value.id in get_tile_family(
                        self.project.tile_family
                    ).supported_border_presets()
                )
            ],
            "fixed_grout_mm": DESIGNER_GROUT_MM,
            "selected_canvas_id": self.canvas_id,
            "selected_tile_shape": self.tile_shape,
            "selected_tile_id": self.tile_id,
            "selected_tile_orientation": self.tile_orientation,
            "custom_grid_max": CUSTOM_GRID_MAX,
            "document": {
                "title": self.document_title,
                "dirty": self.document_dirty,
                "has_file": self.document_path is not None,
            },
            "project": project_payload,
        }

    def _canvas_preview_payload(self, body: dict) -> dict:
        if self.tile_id is None or self.tile_orientation is None:
            raise ValueError("Configure the tile system before previewing a canvas.")
        canvas_id = body.get("canvas_id")
        shell = (
            DesignerProjectShell.create_physical(
                self.tile_id, self.tile_orientation,
                float(body.get("width_in")), float(body.get("height_in")),
                family_id=self.tile_shape,
            )
            if canvas_id == "custom" and body.get("width_in") is not None
            else DesignerProjectShell.create_custom(
                self.tile_id, self.tile_orientation,
                body.get("tiles_across"), body.get("tiles_down"),
                family_id=self.tile_shape,
            )
            if canvas_id == "custom"
            else DesignerProjectShell.create(
                canvas_id, self.tile_id, self.tile_orientation,
                family_id=self.tile_shape,
            )
        )
        return {
            "canvas_id": canvas_id,
            "width_in": shell.geometry.width_in,
            "height_in": shell.geometry.height_in,
            "visible_piece_count": sum(
                value.piece_type != "outside" for value in shell.geometry.placements
            ),
        }

    def _require_project(self) -> DesignerProjectShell:
        if self.project is None:
            raise ValueError("Create a Designer project before editing artwork.")
        return self.project

    def _require_artwork(self) -> DesignerArtwork:
        if self.artwork is None:
            raise ValueError("No SVG artwork is loaded.")
        return self.artwork

    def _project_file_state(self, title: str | None = None) -> DesignerProjectFileState:
        return DesignerProjectFileState(
            project=self._require_project(), artwork=self.artwork,
            generated_artwork=self.generated_artwork,
            paint_overrides=dict(self.paint_overrides),
            artwork_edit_mode=self.artwork_edit_mode,
            title=title or self.document_title,
        )

    def _document_state_payload(self, *, saved: bool = False) -> dict:
        return {
            "payload_kind": "document_state",
            "stage": "workspace" if self.project is not None else self.payload()["stage"],
            "document": {
                "title": self.document_title,
                "dirty": self.document_dirty,
                "has_file": self.document_path is not None,
            },
            "saved": saved,
        }

    def _save_project_document(self, body: dict, *, save_as: bool) -> dict:
        self._require_project()
        requested = body.get("path")
        destination = Path(requested) if isinstance(requested, str) and requested else None
        if destination is None and not save_as and self.document_path is not None:
            destination = self.document_path
        if destination is None:
            destination = self._project_save_dialog(self.document_path)
        if destination is None:
            return {**self._document_state_payload(), "cancelled": True}
        destination = normalize_project_path(destination)
        title = destination.stem.strip() or "Untitled"
        saved_path = save_project_file(
            destination, self._project_file_state(title),
        )
        self.document_path = saved_path.resolve()
        self.document_title = title
        self.document_dirty = False
        return self._document_state_payload(saved=True)

    def _open_project_document(self, body: dict) -> dict:
        requested = body.get("path")
        source = Path(requested) if isinstance(requested, str) and requested else None
        if source is None:
            source = self._project_open_dialog()
        if source is None:
            return {**self._document_state_payload(), "cancelled": True}
        loaded = load_project_file(source)
        self.project = loaded.project
        self.artwork = loaded.artwork
        self.generated_artwork = loaded.generated_artwork
        self.paint_overrides = dict(loaded.paint_overrides)
        self.artwork_edit_mode = loaded.artwork_edit_mode
        self.tile_shape = self.project.tile_family
        self.tile_id = self.project.tile.id
        self.tile_orientation = self.project.tile_orientation
        self.canvas_id = self.project.canvas.id
        self._canvas_confirmed = False
        self._custom_width_in = self.project.canvas.width_in
        self._custom_height_in = self.project.canvas.height_in
        self.document_path = Path(source).expanduser().resolve()
        self.document_title = self.document_path.stem
        self.document_dirty = False
        self.welcome_active = False
        self._canvas_first_setup = False
        self._setup_stage = None
        return self.payload()

    def _export_snapshot(self) -> DesignerExportSnapshot:
        return DesignerExportSnapshot(
            project=self._require_project(),
            generated_artwork=self.generated_artwork,
            paint_overrides=dict(self.paint_overrides),
            document_title=self.document_title,
        )

    def _start_export_job(self, mode: str, export_kind: str = "print_package") -> dict:
        if export_kind not in {"print_package", "stl"}:
            raise ValueError("Unknown fabrication export type.")
        if (
            self._active_export_job_id is not None
            and self._export_jobs[self._active_export_job_id]["status"] == "running"
        ):
            raise ValueError("A fabrication export is already in progress.")
        snapshot = self._export_snapshot()
        # Validate the selected mode before reserving an output directory.
        preview = self._export_preview(snapshot, mode)
        try:
            suffix = (
                f"STL {preview['mode']['display_name']}"
                if export_kind == "stl" else None
            )
            output = (
                self.export_service.allocate_named_output_directory(
                    snapshot.document_title, suffix,
                )
                if export_kind == "stl"
                else self.export_service.allocate_output_directory(
                    snapshot.document_title,
                )
            )
        except PermissionError as exc:
            raise RuntimeError(
                "Mosaica cannot write to the export location. Check folder permissions."
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                "Mosaica could not create a new export folder in Downloads."
            ) from exc
        job_id = f"export-{self._next_export_job:04d}"
        self._next_export_job += 1
        job = {
            "job_id": job_id,
            "status": "running",
            "kind": export_kind,
            "mode": preview["mode"],
            "panel_count": preview["panel_count"],
            "message": "Preparing your fabrication package…",
            "progress": {
                "phase": "preparing",
                "current_panel": None,
                "completed_panels": 0,
                "total_panels": preview["panel_count"],
                "panel_index": None,
                "message": "Preparing your fabrication package…",
            },
            "progress_events": [],
            "output_directory": str(output),
            "result": None,
            "error": None,
        }
        self._export_jobs[job_id] = job
        self._active_export_job_id = job_id
        Thread(
            target=self._run_export_job,
            args=(job_id, snapshot, mode, output, export_kind),
            daemon=True,
            name=f"mosaica-{job_id}",
        ).start()
        return dict(job)

    def _export_preview(
        self, snapshot: DesignerExportSnapshot, mode: str,
    ) -> dict:
        from .fabricate.panelize import PanelizationError

        try:
            return self.export_service.preview(snapshot, mode)
        except PanelizationError as exc:
            _EXPORT_LOG.exception("Designer panelization preview failed: %s", exc)
            raise RuntimeError(
                "Mosaica could not divide this design into printable panels. "
                "Review the project dimensions and try again."
            ) from exc

    def _run_export_job(
        self,
        job_id: str,
        snapshot: DesignerExportSnapshot,
        mode: str,
        output: Path,
        export_kind: str = "print_package",
    ) -> None:
        from .fabricate.panelize import PanelizationError

        try:
            generator = (
                self.export_service.generate_stl
                if export_kind == "stl" else self.export_service.generate
            )
            result = generator(
                snapshot, mode, output,
                progress=lambda event: self._update_export_progress(job_id, event),
            )
        except PanelizationError as exc:
            message = (
                "Mosaica could not divide this design into printable panels. "
                "Review the project dimensions and try again."
            )
            _EXPORT_LOG.exception(
                "Designer panelization failed: %s; progress=%s",
                exc, self._export_jobs[job_id]["progress"],
            )
        except PermissionError as exc:
            message = (
                "Mosaica lost permission to write the export. "
                "Check the Downloads folder and try again."
            )
            _EXPORT_LOG.exception(
                "Designer export permission failure: %s; progress=%s",
                exc, self._export_jobs[job_id]["progress"],
            )
        except OSError as exc:
            message = (
                "Mosaica could not finish writing the export package. "
                "Check available disk space and folder permissions."
            )
            _EXPORT_LOG.exception(
                "Designer export filesystem failure: %s; progress=%s",
                exc, self._export_jobs[job_id]["progress"],
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            message = f"Mosaica could not prepare this design for fabrication: {exc}"
            _EXPORT_LOG.exception(
                "Designer fabrication resolution failed: %s; progress=%s",
                exc, self._export_jobs[job_id]["progress"],
            )
        except Exception as exc:
            message = (
                "Mosaica could not generate the fabrication package. "
                "No existing export was replaced."
            )
            _EXPORT_LOG.exception(
                "Unexpected Designer export failure: %s; progress=%s",
                exc, self._export_jobs[job_id]["progress"],
            )
        else:
            with self._state_lock:
                job = self._export_jobs[job_id]
                job.update({
                    "status": "complete",
                    "message": "Your mosaic is ready to print.",
                    "progress": {
                        **job["progress"],
                        "phase": "complete",
                        "current_panel": None,
                        "completed_panels": job["panel_count"],
                        "message": "Complete",
                    },
                    "result": result.to_dict(),
                })
                self._active_export_job_id = None
            return

        shutil.rmtree(output, ignore_errors=True)
        with self._state_lock:
            job = self._export_jobs[job_id]
            job.update({
                "status": "error",
                "message": "Export failed.",
                "error": message,
                "output_directory": None,
                "progress": {
                    **job["progress"], "phase": "error", "message": message,
                },
            })
            self._active_export_job_id = None

    def _update_export_progress(
        self, job_id: str, event: dict[str, object],
    ) -> None:
        with self._state_lock:
            job = self._export_jobs[job_id]
            progress = dict(event)
            job["progress"] = progress
            job["message"] = progress["message"]
            job["progress_events"].append(progress)

    def _export_job(self, job_id: str | None) -> dict:
        if not job_id or job_id not in self._export_jobs:
            raise ValueError("Unknown fabrication export job.")
        return dict(self._export_jobs[job_id])

    def _artwork_state_payload(self) -> dict:
        """Authoritative compact state for mutations that cannot alter tiles."""

        return {
            "payload_kind": "artwork_state",
            "stage": "workspace",
            "document": {
                "title": self.document_title,
                "dirty": self.document_dirty,
                "has_file": self.document_path is not None,
            },
            "artwork": (
                {**self.artwork.to_dict(), "edit_mode": self.artwork_edit_mode}
                if self.artwork is not None else None
            ),
            "generated_artwork": (
                self._generated_artwork_summary(
                    self.generated_artwork.to_dict()
                )
                if self.generated_artwork is not None else None
            ),
        }

    def _design_state_payload(self, previous_project: dict) -> dict:
        """Return changed visual state without immutable physical geometry."""

        project = self._require_project().to_dict(
            self.generated_artwork, self.paint_overrides,
        )
        previous_tiles = {
            value["id"]: value for value in previous_project["geometry"]["tiles"]
        }
        dynamic_fields = (
            "color_role",
            "color_id",
            "display_color",
            "border_owned",
            "protected",
            "artwork_available",
            "generated_artwork",
            "lower_color_id",
            "lower_display_color",
            "manual_override",
            "editable",
        )
        tile_updates = []
        for tile in project["geometry"]["tiles"]:
            previous = previous_tiles.get(tile["id"])
            update = {"id": tile["id"]}
            update.update({key: tile[key] for key in dynamic_fields})
            if previous is None or any(
                previous.get(key) != update[key] for key in dynamic_fields
            ):
                tile_updates.append(update)

        generated = project["generated_artwork"]
        generated_summary = self._generated_artwork_summary(generated)
        return {
            "payload_kind": "design_state",
            "stage": "workspace",
            "document": {
                "title": self.document_title,
                "dirty": self.document_dirty,
                "has_file": self.document_path is not None,
            },
            "artwork": (
                {**self.artwork.to_dict(), "edit_mode": self.artwork_edit_mode}
                if self.artwork is not None else None
            ),
            "generated_artwork": generated_summary,
            "grout": project["grout"],
            "border": project["border"],
            "color_system": project["color_system"],
            "color_counts": project["color_counts"],
            "paint": project["paint"],
            "tile_updates": tile_updates,
        }

    @staticmethod
    def _generated_artwork_summary(generated: dict | None) -> dict | None:
        if generated is None:
            return None
        return {
            key: value for key, value in generated.items()
            if key != "assignments"
        }

    @staticmethod
    def _request_json(environ) -> dict:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        value = json.loads(environ["wsgi.input"].read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request JSON must be an object.")
        return value

    @staticmethod
    def _json(start_response, status: str, value: dict):
        body = json.dumps(value).encode("utf-8")
        start_response(status, [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    @staticmethod
    def _asset(path: str, start_response):
        filename, content_type = DESIGNER_ASSETS[path]
        body = files("mosaica").joinpath("web", filename).read_bytes()
        start_response("200 OK", [
            ("Content-Type", content_type),
            ("Cache-Control", "no-store"),
            ("Content-Length", str(len(body))),
        ])
        return [body]


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Thread-per-request localhost server with bounded process lifetime."""

    daemon_threads = True


class DesignerServerHandler(SimpleHandler):
    """WSGI handler that records whether the complete response was flushed."""

    def cleanup_headers(self):
        super().cleanup_headers()
        # Connection is a hop-by-hop header and therefore must be added by
        # the HTTP handler rather than the WSGI application.
        self.headers["Connection"] = "close"

    def finish_response(self):
        completed = False
        error = None
        try:
            if not self.result_is_file() or not self.sendfile():
                for data in self.result:
                    self.write(data)
                self.finish_content()
            self.stdout.flush()
            completed = True
        except Exception as exc:
            error = exc
            if hasattr(self.result, "close"):
                self.result.close()
            raise
        finally:
            headers = dict(self.headers.items()) if self.headers is not None else {}
            _TRANSPORT_LOG.info(
                "designer_response endpoint=%s status=%s bytes=%s "
                "content_type=%s content_length=%s connection=%s "
                "transfer_encoding=%s write_completed=%s error=%r",
                self.environ.get("PATH_INFO") if self.environ else None,
                self.status,
                self.bytes_sent,
                headers.get("Content-Type"),
                headers.get("Content-Length"),
                headers.get("Connection"),
                headers.get("Transfer-Encoding"),
                completed,
                error,
            )
        if completed:
            self.close()


class DesignerRequestHandler(WSGIRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = ""
            self.request_version = ""
            self.command = ""
            self.send_error(414)
            return
        if not self.parse_request():
            return
        handler = DesignerServerHandler(
            self.rfile,
            self.wfile,
            self.get_stderr(),
            self.get_environ(),
            multithread=True,
        )
        handler.http_version = "1.1"
        handler.request_handler = self
        handler.run(self.server.get_app())


def run_designer(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Mosaica may bind to localhost only.")
    app = MosaicDesignerApp()
    url = f"http://127.0.0.1:{port}/"
    try:
        server = make_server(
            "127.0.0.1",
            port,
            app,
            server_class=ThreadingWSGIServer,
            handler_class=DesignerRequestHandler,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Cannot start Mosaica on 127.0.0.1:{port}; "
            "the requested port is unavailable."
        ) from exc
    with server:
        print(f"Mosaica: {url}")
        print("Press Ctrl+C to stop.")
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()

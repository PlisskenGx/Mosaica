from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.resources import files
import json
import logging
from math import ceil, sqrt
from socketserver import ThreadingMixIn
from threading import RLock
import webbrowser
from wsgiref.handlers import SimpleHandler
from wsgiref.simple_server import (
    WSGIRequestHandler,
    WSGIServer,
    make_server,
)

from .geometry import GridGeometry, build_panel_geometry
from .model import MosaicConfig
from .border import (
    BORDER_PRESETS,
    build_border_layer,
    border_preset,
)
from .designer_colors import DEFAULT_DESIGNER_COLORS, DesignerColorResolution
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


MM_PER_INCH = 25.4
DESIGNER_GROUT_MM = 1.8
P1S_BUILD_AREA_MM = 256.0
CANVAS_PREVIEW_REM_PER_INCH = 0.10
_TRANSPORT_LOG = logging.getLogger("mosaic_engine.designer.transport")
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


@dataclass(frozen=True)
class TilePreset:
    id: str
    flat_to_flat_mm: float
    title: str
    summary: str
    recommended: bool = False

    @property
    def flat_to_flat_in(self) -> float:
        return self.flat_to_flat_mm / MM_PER_INCH

    @property
    def side_length_mm(self) -> float:
        return self.flat_to_flat_mm / sqrt(3.0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "flat_to_flat_mm": self.flat_to_flat_mm,
            "flat_to_flat_in": self.flat_to_flat_in,
            "side_length_mm": self.side_length_mm,
            "title": self.title,
            "summary": self.summary,
            "recommended": self.recommended,
        }


CANVAS_PRESETS = (
    CanvasPreset("square-s", "Square S", 24.0, 24.0),
    CanvasPreset("square-m", "Square M", 36.0, 36.0),
    CanvasPreset("square-l", "Square L", 48.0, 48.0),
    CanvasPreset("landscape", "Landscape", 48.0, 30.0),
    CanvasPreset("wide", "Wide", 60.0, 30.0),
    CanvasPreset("panoramic", "Panoramic", 72.0, 30.0),
)

TILE_PRESETS = (
    TilePreset("s", 20.0, "Detail", "More detail · More pieces"),
    TilePreset("m", 24.0, "Balanced", "Balanced detail · Balanced pieces", True),
    TilePreset("l", 28.0, "Bold", "Stronger mosaic · Fewer pieces"),
)

_CANVASES = {value.id: value for value in CANVAS_PRESETS}
_TILES = {value.id: value for value in TILE_PRESETS}


@dataclass(frozen=True)
class DesignerProjectShell:
    canvas: CanvasPreset
    tile: TilePreset
    grout_mm: float
    geometry: GridGeometry
    color_system: DesignerColorResolution = DEFAULT_DESIGNER_COLORS
    border_preset_id: str = "none"

    @classmethod
    def create(cls, canvas_id: str, tile_id: str) -> DesignerProjectShell:
        try:
            canvas = _CANVASES[canvas_id]
        except KeyError as exc:
            raise ValueError(f"Unknown canvas preset: {canvas_id}") from exc
        try:
            tile = _TILES[tile_id]
        except KeyError as exc:
            raise ValueError(f"Unknown tile preset: {tile_id}") from exc
        config = MosaicConfig(
            tile_shape="hex",
            tile_width_in=tile.flat_to_flat_in,
            tile_height_in=tile.flat_to_flat_in,
            grout_width_in=DESIGNER_GROUT_MM / MM_PER_INCH,
            hex_orientation="pointy",
            target_width_in=canvas.width_in,
            target_height_in=canvas.height_in,
        )
        geometry = build_panel_geometry(config, canvas.width_in, canvas.height_in)
        return cls(canvas, tile, DESIGNER_GROUT_MM, geometry)

    def with_border(self, preset_id: str) -> DesignerProjectShell:
        border_preset(preset_id)
        return replace(self, border_preset_id=preset_id)

    def with_color_system(
        self, color_system: DesignerColorResolution,
    ) -> DesignerProjectShell:
        return replace(self, color_system=color_system)

    def to_dict(
        self,
        generated_artwork: DesignerGeneratedArtwork | None = None,
        paint_overrides: dict[str, str] | None = None,
    ) -> dict:
        paint_overrides = paint_overrides or {}
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
        lower_color_ids = {
            tile_id: (
                generated_assignments[tile_id].color_id
                if tile_id in generated_assignments and tile_id in available
                else self.color_system.resolve(role).color_id
            )
            for tile_id, role in effective_roles.items()
        }
        effective_color_ids = {
            tile_id: (
                paint_overrides[tile_id]
                if tile_id in paint_overrides and tile_id in available
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
        return {
            "canvas_preset": self.canvas.to_dict(),
            "tile_preset": self.tile.to_dict(),
            "grout_mm": self.grout_mm,
            "color_system": effective_resolution.to_dict(),
            "color_counts": [value.to_dict() for value in color_counts],
            "paint": {
                "overrides": dict(sorted(paint_overrides.items())),
                "override_count": len(paint_overrides),
            },
            "border": border.to_dict(),
            "generated_artwork": (
                {
                    **generated_artwork.to_dict(),
                    "display_active": True,
                }
                if generated_artwork is not None else None
            ),
            "print_plate_estimate": estimate_minimum_print_plates(
                self.canvas.width_in,
                self.canvas.height_in,
            ),
            "geometry": {
                "shape": self.geometry.shape,
                "orientation": "pointy",
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
                "tiles": [
                    {
                        "id": f"placement-{index:06d}",
                        "row": placement.row,
                        "column": placement.column,
                        "piece_type": placement.piece_type,
                        "piece_fraction": placement.piece_fraction,
                        "vertices_in": [list(point) for point in placement.vertices_in],
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
                        "editable": (
                            placement.piece_type == "full"
                            and f"placement-{index:06d}" in available
                        ),
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

    def __init__(self) -> None:
        self._state_lock = RLock()
        self.canvas_id: str | None = None
        self.project: DesignerProjectShell | None = None
        self.artwork: DesignerArtwork | None = None
        self.generated_artwork: DesignerGeneratedArtwork | None = None
        self.paint_overrides: dict[str, str] = {}
        self.artwork_edit_mode = True
        self.document_title = "Untitled"
        self.document_dirty = False

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
            if method == "POST" and path == "/api/designer/canvas":
                canvas_id = self._request_json(environ).get("canvas_id")
                if canvas_id not in _CANVASES:
                    raise ValueError(f"Unknown canvas preset: {canvas_id}")
                self.canvas_id = canvas_id
                self.project = None
                self.artwork = None
                self.generated_artwork = None
                self.paint_overrides = {}
                self.document_dirty = False
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/tile":
                if self.canvas_id is None:
                    raise ValueError("Select a canvas preset before a tile preset.")
                tile_id = self._request_json(environ).get("tile_id")
                self.project = DesignerProjectShell.create(self.canvas_id, tile_id)
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
                    raise ValueError("Paint requires a list of placement IDs.")
                if mode not in {"paint", "restore"}:
                    raise ValueError("Paint mode must be paint or restore.")
                unique_ids = tuple(dict.fromkeys(placement_ids))
                border = build_border_layer(
                    project.geometry, project.border_preset_id,
                )
                editable = set(border.available_artwork_placement_ids)
                invalid = [value for value in unique_ids if value not in editable]
                if invalid:
                    raise ValueError(
                        "Paint tiles must be editable full placements: "
                        + ", ".join(invalid)
                    )
                color_id = body.get("color_id")
                if mode == "paint":
                    project.color_system.by_id(color_id)
                updated = dict(self.paint_overrides)
                if mode == "paint":
                    updated.update({value: color_id for value in unique_ids})
                else:
                    for value in unique_ids:
                        updated.pop(value, None)
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
            if method == "POST" and path == "/api/designer/back":
                if self.project is not None:
                    self.project = None
                    self.artwork = None
                    self.generated_artwork = None
                    self.paint_overrides = {}
                    self.artwork_edit_mode = True
                    self.document_dirty = False
                else:
                    self.canvas_id = None
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
        return {
            "stage": (
                "workspace" if self.project is not None
                else "tile" if self.canvas_id is not None
                else "canvas"
            ),
            "canvas_presets": [value.to_dict() for value in CANVAS_PRESETS],
            "tile_presets": [value.to_dict() for value in TILE_PRESETS],
            "border_presets": [value.to_dict() for value in BORDER_PRESETS],
            "fixed_grout_mm": DESIGNER_GROUT_MM,
            "selected_canvas_id": self.canvas_id,
            "document": {
                "title": self.document_title,
                "dirty": self.document_dirty,
            },
            "project": project_payload,
        }

    def _require_project(self) -> DesignerProjectShell:
        if self.project is None:
            raise ValueError("Create a Designer project before editing artwork.")
        return self.project

    def _require_artwork(self) -> DesignerArtwork:
        if self.artwork is None:
            raise ValueError("No SVG artwork is loaded.")
        return self.artwork

    def _artwork_state_payload(self) -> dict:
        """Authoritative compact state for mutations that cannot alter tiles."""

        return {
            "payload_kind": "artwork_state",
            "stage": "workspace",
            "document": {
                "title": self.document_title,
                "dirty": self.document_dirty,
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
            },
            "artwork": (
                {**self.artwork.to_dict(), "edit_mode": self.artwork_edit_mode}
                if self.artwork is not None else None
            ),
            "generated_artwork": generated_summary,
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
        body = files("mosaic_engine").joinpath("web", filename).read_bytes()
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

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.resources import files
import json
from math import ceil, sqrt
import webbrowser
from wsgiref.simple_server import make_server

from .geometry import GridGeometry, build_panel_geometry
from .model import MosaicConfig
from .border import (
    BORDER_PRESETS,
    build_border_layer,
    border_preset,
)
from .designer_colors import DEFAULT_DESIGNER_COLORS


MM_PER_INCH = 25.4
DESIGNER_GROUT_MM = 1.8
P1S_BUILD_AREA_MM = 256.0
CANVAS_PREVIEW_REM_PER_INCH = 0.10


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

    def to_dict(self) -> dict:
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
        color_counts = DEFAULT_DESIGNER_COLORS.count_visible(
            (
                placement.piece_type,
                effective_roles[f"placement-{index:06d}"],
            )
            for index, placement in enumerate(self.geometry.placements)
        )
        return {
            "canvas_preset": self.canvas.to_dict(),
            "tile_preset": self.tile.to_dict(),
            "grout_mm": self.grout_mm,
            "color_system": DEFAULT_DESIGNER_COLORS.to_dict(),
            "color_counts": [value.to_dict() for value in color_counts],
            "border": border.to_dict(),
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
                        "color_id": DEFAULT_DESIGNER_COLORS.resolve(
                            effective_roles[f"placement-{index:06d}"]
                        ).color_id,
                        "display_color": DEFAULT_DESIGNER_COLORS.resolve(
                            effective_roles[f"placement-{index:06d}"]
                        ).display_color,
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
        self.canvas_id: str | None = None
        self.project: DesignerProjectShell | None = None
        self.document_title = "Untitled"
        self.document_dirty = False

    def __call__(self, environ, start_response):
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
                self.document_dirty = False
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/tile":
                if self.canvas_id is None:
                    raise ValueError("Select a canvas preset before a tile preset.")
                tile_id = self._request_json(environ).get("tile_id")
                self.project = DesignerProjectShell.create(self.canvas_id, tile_id)
                self.document_dirty = False
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/border":
                if self.project is None:
                    raise ValueError("Create a Designer project before selecting a border.")
                preset_id = self._request_json(environ).get("preset_id")
                changed = preset_id != self.project.border_preset_id
                self.project = self.project.with_border(preset_id)
                self.document_dirty = self.document_dirty or changed
                return self._json(start_response, "200 OK", self.payload())
            if method == "POST" and path == "/api/designer/back":
                if self.project is not None:
                    self.project = None
                    self.document_dirty = False
                else:
                    self.canvas_id = None
                return self._json(start_response, "200 OK", self.payload())
            return self._json(start_response, "404 Not Found", {"error": "Not found."})
        except (KeyError, TypeError, ValueError) as exc:
            return self._json(start_response, "400 Bad Request", {"error": str(exc)})

    def payload(self) -> dict:
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
            "project": self.project.to_dict() if self.project is not None else None,
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
            ("Content-Length", str(len(body))),
        ])
        return [body]


def run_designer(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Mosaic Designer may bind to localhost only.")
    app = MosaicDesignerApp()
    url = f"http://127.0.0.1:{port}/"
    try:
        server = make_server("127.0.0.1", port, app)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot start Mosaic Designer on 127.0.0.1:{port}; "
            "the requested port is unavailable."
        ) from exc
    with server:
        print(f"Mosaic Designer: {url}")
        print("Press Ctrl+C to stop.")
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()

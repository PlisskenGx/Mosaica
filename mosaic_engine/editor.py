from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
from urllib.parse import unquote
import webbrowser
from wsgiref.simple_server import make_server

from .project import MosaicProject


JSON_HEADERS = [
    ("Content-Type", "application/json; charset=utf-8"),
    ("Cache-Control", "no-store"),
]

ASSETS = {
    "/": ("editor.html", "text/html; charset=utf-8"),
    "/editor.css": ("editor.css", "text/css; charset=utf-8"),
    "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
}


class MosaicEditorApp:
    """Small WSGI application for editing one saved project."""

    def __init__(self, project_path: str | Path) -> None:
        self.project_path = Path(project_path).resolve(strict=False)
        self.project = MosaicProject.load(self.project_path)
        self.dirty = False

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = unquote(environ.get("PATH_INFO", "/"))

        try:
            if method == "GET" and path in ASSETS:
                return self._asset(path, start_response)

            if method == "GET" and path == "/api/project":
                return self._json(
                    start_response,
                    "200 OK",
                    self.project_payload(),
                )

            if method == "POST" and path == "/api/save":
                self.project.save(self.project_path)
                self.dirty = False
                return self._json(
                    start_response,
                    "200 OK",
                    {
                        "saved": True,
                        "path": str(self.project_path),
                        "dirty": self.dirty,
                    },
                )

            if (
                method == "POST"
                and path == "/api/overrides/clear-all"
            ):
                had_overrides = bool(self.project.overrides)
                self.project.clear_all_overrides()
                self.dirty = self.dirty or had_overrides
                return self._json(
                    start_response,
                    "200 OK",
                    self.project_payload(),
                )

            tile_action = self._tile_action(path)

            if method == "POST" and tile_action is not None:
                row, column, action = tile_action

                if action == "override":
                    body = self._request_json(environ)
                    palette_index = body.get("palette_index")

                    if not isinstance(palette_index, int):
                        raise ValueError(
                            "palette_index must be an integer."
                        )

                    previous = self.project.override_value(row, column)
                    self.project.set_override(
                        row,
                        column,
                        palette_index,
                    )
                    self.dirty = (
                        self.dirty
                        or previous != palette_index
                    )
                elif action == "clear":
                    if not self._tile_payload(row, column)["editable"]:
                        raise ValueError("Tile is protected from editing.")

                    previous = self.project.override_value(row, column)
                    self.project.clear_override(row, column)
                    self.dirty = self.dirty or previous is not None
                else:
                    return self._not_found(start_response)

                return self._json(
                    start_response,
                    "200 OK",
                    self.project_payload(),
                )

            return self._not_found(start_response)

        except (IndexError, KeyError, ValueError) as exc:
            return self._json(
                start_response,
                "400 Bad Request",
                {"error": str(exc)},
            )

    def project_payload(self) -> dict:
        return {
            "project": {
                "path": str(self.project_path),
                "source_filename": self.project.source_path.name,
            },
            "panel": {
                "width_in": self.project.physical_width_in,
                "height_in": self.project.physical_height_in,
            },
            "palette": [
                {
                    "index": index,
                    "name": color.name,
                    "rgb": list(color.rgb),
                    "hex": "#%02X%02X%02X" % color.rgb,
                    "sku": color.sku,
                }
                for index, color in enumerate(self.project.palette)
            ],
            "counts": self.project.counts(),
            "tiles": [
                self._tile_payload(
                    placement.row,
                    placement.column,
                )
                for placement in self.project.geometry.placements
                if placement.piece_type != "outside"
            ],
            "overrides_count": len(self.project.overrides),
            "dirty": self.dirty,
        }

    def _tile_payload(self, row: int, column: int) -> dict:
        placement = self.project.geometry.placement(row, column)
        generated_index = self.project.generated_value(row, column)
        override_index = self.project.override_value(row, column)
        effective_index = self.project.effective_index(row, column)
        editable = (
            placement.piece_type != "outside"
            and (
                placement.piece_type == "full"
                or not self.project.protect_perimeter
            )
        )

        return {
            "id": (
                "placement-"
                f"{row * self.project.columns + column:06d}"
            ),
            "row": row,
            "column": column,
            "display_row": row + 1,
            "display_column": column + 1,
            "piece_type": placement.piece_type,
            "piece_fraction": placement.piece_fraction,
            "editable": editable,
            "vertices_in": [
                [x, y]
                for x, y in placement.vertices_in
            ],
            "generated_index": generated_index,
            "override_index": override_index,
            "effective_index": effective_index,
        }

    @staticmethod
    def _tile_action(path: str):
        parts = path.strip("/").split("/")

        if len(parts) != 5 or parts[:2] != ["api", "tiles"]:
            return None

        try:
            return int(parts[2]), int(parts[3]), parts[4]
        except ValueError:
            return None

    @staticmethod
    def _request_json(environ) -> dict:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc

        raw = environ["wsgi.input"].read(length)

        try:
            value = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc

        if not isinstance(value, dict):
            raise ValueError("Request JSON must be an object.")

        return value

    @staticmethod
    def _json(start_response, status: str, value: dict):
        body = json.dumps(value).encode("utf-8")
        start_response(
            status,
            JSON_HEADERS + [("Content-Length", str(len(body)))],
        )
        return [body]

    @staticmethod
    def _asset(path: str, start_response):
        filename, content_type = ASSETS[path]
        body = (
            files("mosaic_engine")
            .joinpath("web", filename)
            .read_bytes()
        )
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    @classmethod
    def _not_found(cls, start_response):
        return cls._json(
            start_response,
            "404 Not Found",
            {"error": "Not found."},
        )


def run_editor(
    project_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The mosaic editor may bind to localhost only.")

    app = MosaicEditorApp(project_path)
    url = f"http://127.0.0.1:{port}/"

    try:
        server = make_server("127.0.0.1", port, app)
    except OSError as exc:
        raise RuntimeError(
            "Cannot start mosaic editor on "
            f"127.0.0.1:{port}; the requested port is unavailable."
        ) from exc

    with server:
        print(f"Mosaic editor: {url}")
        print("Press Ctrl+C to stop.")

        if open_browser:
            webbrowser.open(url)

        server.serve_forever()

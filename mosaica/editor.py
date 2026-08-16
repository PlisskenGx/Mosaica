from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
from urllib.parse import unquote
import webbrowser
from wsgiref.simple_server import make_server

from .evidence import resolve_project_bw_evidence
from .contour_refinement import (
    ContourRefinementReport,
    generate_contour_refinement_proposals,
)
from .project import MosaicProject
from .processing import palette_extremes
from .refinement import (
    RefinementProposal,
    RefinementReport,
    generate_refinement_proposals,
)


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

    def __init__(
        self,
        project_path: str | Path,
        *,
        refinement_report: RefinementReport | None = None,
        contour_report: ContourRefinementReport | None = None,
    ) -> None:
        self.project_path = Path(project_path).resolve(strict=False)
        self.project = MosaicProject.load(self.project_path)
        self.dirty = False
        self._refinement_report = refinement_report
        self._contour_report = contour_report
        self._evidence = None
        self._review_state: dict[str, str] = {}
        self._tile_coordinates = {
            self._tile_id(index): (
                placement.row,
                placement.column,
            )
            for index, placement
            in enumerate(self.project.geometry.placements)
        }
        self._tile_ids = {
            coordinate: tile_id
            for tile_id, coordinate
            in self._tile_coordinates.items()
        }

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

            if method == "GET" and path == "/api/proposals":
                return self._json(
                    start_response,
                    "200 OK",
                    self._proposal_list_payload(),
                )

            if method == "GET" and path == "/api/proposals/session":
                return self._json(
                    start_response,
                    "200 OK",
                    self._review_summary(),
                )

            if method == "GET" and path == "/api/contour-proposals":
                return self._json(
                    start_response,
                    "200 OK",
                    self._ensure_contours().to_dict(),
                )

            if (
                method == "GET"
                and path.startswith("/api/contour-proposals/")
            ):
                candidate_id = path.rsplit("/", 1)[-1]
                for candidate in self._ensure_contours().candidates:
                    if candidate.candidate_id == candidate_id:
                        return self._json(
                            start_response,
                            "200 OK",
                            candidate.to_dict(),
                        )
                raise ValueError(f"Unknown contour candidate: {candidate_id}")

            proposal_action = self._proposal_action(path)

            if method == "GET" and proposal_action is not None:
                candidate_id, alternative, action = proposal_action
                if alternative is not None or action is not None:
                    return self._not_found(start_response)
                return self._json(
                    start_response,
                    "200 OK",
                    self._candidate_payload(candidate_id),
                )

            if method == "POST" and path == "/api/proposals/reset":
                self._review_state.clear()
                return self._json(
                    start_response,
                    "200 OK",
                    self._proposal_list_payload(),
                )

            if method == "POST" and proposal_action is not None:
                candidate_id, alternative, action = proposal_action
                if action == "accept" and alternative is not None:
                    body = self._request_json(environ)
                    return self._accept_proposal(
                        start_response,
                        candidate_id,
                        alternative,
                        body.get("confirm_conflicts") is True,
                    )
                if alternative is None and action in {"reject", "skip"}:
                    self._candidate(candidate_id)
                    self._review_state[candidate_id] = (
                        "rejected" if action == "reject" else "skipped"
                    )
                    return self._json(
                        start_response,
                        "200 OK",
                        self._proposal_list_payload(),
                    )
                return self._not_found(start_response)

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

            if method == "POST" and path == "/api/overrides/batch":
                body = self._request_json(environ)
                palette_index = body.get("palette_index")

                if not isinstance(palette_index, int):
                    raise ValueError(
                        "palette_index must be an integer."
                    )

                if not 0 <= palette_index < len(self.project.palette):
                    raise ValueError(
                        "Palette index is outside the project palette."
                    )

                coordinates = self._batch_coordinates(body)
                changed = any(
                    self.project.override_value(row, column)
                    != palette_index
                    for row, column in coordinates
                )

                for row, column in coordinates:
                    self.project.set_override(
                        row,
                        column,
                        palette_index,
                    )

                if changed:
                    self.dirty = True

                return self._json(
                    start_response,
                    "200 OK",
                    self.project_payload(),
                )

            if (
                method == "POST"
                and path == "/api/overrides/batch-clear"
            ):
                body = self._request_json(environ)
                coordinates = self._batch_coordinates(body)
                changed = any(
                    self.project.override_value(row, column)
                    is not None
                    for row, column in coordinates
                )

                for row, column in coordinates:
                    self.project.clear_override(row, column)

                if changed:
                    self.dirty = True

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

        except ProposalConflictError as exc:
            return self._json(
                start_response,
                "409 Conflict",
                {
                    "error": str(exc),
                    "conflicts": exc.conflicts,
                },
            )
        except (IndexError, KeyError, ValueError, RuntimeError, OSError) as exc:
            return self._json(
                start_response,
                "400 Bad Request",
                {"error": str(exc)},
            )

    def _ensure_proposals(self) -> RefinementReport:
        if self._refinement_report is None:
            self._refinement_report = generate_refinement_proposals(
                self.project,
                self._source_evidence(),
            )
        return self._refinement_report

    def _source_evidence(self):
        if self._evidence is None:
            self._evidence = resolve_project_bw_evidence(self.project)
        return self._evidence

    def _ensure_contours(self) -> ContourRefinementReport:
        if self._contour_report is None:
            self._contour_report = generate_contour_refinement_proposals(
                self.project,
                self._source_evidence(),
            )
        return self._contour_report

    def _candidate(self, candidate_id: str):
        for candidate in self._ensure_proposals().candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise ValueError(f"Unknown candidate ID: {candidate_id}")

    def _candidate_proposals(
        self,
        candidate_id: str,
    ) -> tuple[RefinementProposal, ...]:
        self._candidate(candidate_id)
        return tuple(
            proposal
            for proposal in self._ensure_proposals().proposals
            if proposal.candidate_id == candidate_id
        )

    def _candidate_payload(self, candidate_id: str) -> dict:
        candidate = self._candidate(candidate_id)
        return {
            **candidate.to_dict(),
            "review_status": self._review_state.get(candidate_id),
            "ranked_alternatives": [
                self._proposal_payload(proposal)
                for proposal in self._candidate_proposals(candidate_id)
            ],
        }

    def _proposal_payload(self, proposal: RefinementProposal) -> dict:
        payload = proposal.to_dict()
        dark, light = palette_extremes(list(self.project.palette))
        foreground = light if self.project.config.invert_bw else dark
        for change in payload["changes"]:
            change["change_kind"] = (
                "foreground_addition"
                if change["proposed_index"] == foreground
                else "foreground_removal"
            )
        return payload

    def _proposal_list_payload(self) -> dict:
        report = self._ensure_proposals()
        return {
            "candidates": [
                self._candidate_payload(candidate.candidate_id)
                for candidate in report.candidates
            ],
            "session": self._review_summary(),
        }

    def _review_summary(self) -> dict:
        return {
            "accepted": sum(
                value == "accepted" for value in self._review_state.values()
            ),
            "rejected": sum(
                value == "rejected" for value in self._review_state.values()
            ),
            "skipped": sum(
                value == "skipped" for value in self._review_state.values()
            ),
            "states": dict(sorted(self._review_state.items())),
        }

    def _proposal(
        self,
        candidate_id: str,
        alternative: str,
    ) -> RefinementProposal:
        for proposal in self._candidate_proposals(candidate_id):
            if proposal.alternative == alternative:
                return proposal
        raise ValueError(
            f"Unknown alternative {alternative!r} for {candidate_id}."
        )

    def _accept_proposal(
        self,
        start_response,
        candidate_id: str,
        alternative: str,
        confirm_conflicts: bool,
    ):
        proposal = self._proposal(candidate_id, alternative)
        validated = []
        conflicts = []
        for change in proposal.changes:
            coordinate = self._tile_coordinates.get(change.tile_id)
            if coordinate != (change.row, change.column):
                raise ValueError(
                    f"Proposal contains an invalid tile ID: {change.tile_id}"
                )
            row, column = coordinate
            if not self.project.is_editable(row, column):
                raise ValueError(
                    f"Tile {change.tile_id} is protected from editing."
                )
            if not 0 <= change.proposed_index < len(self.project.palette):
                raise ValueError(
                    f"Proposal palette index for {change.tile_id} is invalid."
                )
            existing = self.project.override_value(row, column)
            if existing is not None and existing != change.proposed_index:
                conflicts.append({
                    "tile_id": change.tile_id,
                    "row": row,
                    "column": column,
                    "existing_override": existing,
                    "proposed_index": change.proposed_index,
                })
            validated.append((row, column, change.proposed_index))
        if conflicts and not confirm_conflicts:
            raise ProposalConflictError(conflicts)

        changed = any(
            self.project.override_value(row, column) != palette_index
            for row, column, palette_index in validated
        )
        for row, column, palette_index in validated:
            self.project.set_override(row, column, palette_index)
        if changed:
            self.dirty = True
        self._review_state[candidate_id] = "accepted"
        return self._json(
            start_response,
            "200 OK",
            {
                "accepted": True,
                "candidate_id": candidate_id,
                "alternative": alternative,
                "project": self.project_payload(),
                "session": self._review_summary(),
            },
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
        editable = self.project.is_editable(row, column)

        return {
            "id": self._tile_ids[(row, column)],
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

    def _batch_coordinates(self, body: dict) -> list[tuple[int, int]]:
        tile_ids = body.get("tile_ids")

        if not isinstance(tile_ids, list) or not tile_ids:
            raise ValueError("tile_ids must be a non-empty list.")

        if (
            any(not isinstance(tile_id, str) for tile_id in tile_ids)
            or len(tile_ids) != len(set(tile_ids))
        ):
            raise ValueError(
                "tile_ids must contain unique stable tile ID strings."
            )

        coordinates: list[tuple[int, int]] = []

        for tile_id in tile_ids:
            coordinate = self._tile_coordinates.get(tile_id)

            if coordinate is None:
                raise ValueError(f"Unknown tile ID: {tile_id}")

            row, column = coordinate

            if not self.project.is_editable(row, column):
                raise ValueError(
                    f"Tile {tile_id} is protected from editing."
                )

            coordinates.append(coordinate)

        return coordinates

    @staticmethod
    def _tile_id(index: int) -> str:
        return f"placement-{index:06d}"

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
    def _proposal_action(path: str):
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[:2] != ["api", "proposals"]:
            return None
        if len(parts) == 3:
            return parts[2], None, None
        if len(parts) == 4 and parts[3] in {"reject", "skip"}:
            return parts[2], None, parts[3]
        if len(parts) == 5 and parts[4] == "accept":
            return parts[2], parts[3], "accept"
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
            files("mosaica")
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


class ProposalConflictError(ValueError):
    def __init__(self, conflicts: list[dict]) -> None:
        super().__init__(
            "Proposal conflicts with existing manual overrides; "
            "explicit confirmation is required."
        )
        self.conflicts = conflicts


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

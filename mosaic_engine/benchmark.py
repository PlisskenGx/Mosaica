from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable

from .evidence import BWEvidence, Coordinate, compute_project_bw_evidence
from .processing import palette_extremes, tile_neighbors
from .project import MosaicProject


@dataclass(frozen=True)
class CorrectionRegion:
    coordinates: tuple[Coordinate, ...]
    black_to_white: int
    white_to_black: int

    @property
    def size(self) -> int:
        return len(self.coordinates)


@dataclass(frozen=True)
class BenchmarkReport:
    project_path: str | None
    full_tiles: int
    stored_overrides: int
    real_changed_overrides: int
    black_to_white: int
    white_to_black: int
    generated_black: int
    effective_black: int
    generated_boundary_edges: int
    effective_boundary_edges: int
    correction_regions: tuple[CorrectionRegion, ...]
    changed_with_opposite_majority: int
    changed_matching_cleanup_rule: int
    generated_same_neighbor_histogram: dict[int, int]
    coverage_disagreements: int | None
    black_to_white_coverage_disagreements: int | None
    white_to_black_coverage_disagreements: int | None
    changed_mean_coverage: float | None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["correction_regions"] = [
            {
                "coordinates": [
                    [row, column]
                    for row, column in region.coordinates
                ],
                "size": region.size,
                "black_to_white": region.black_to_white,
                "white_to_black": region.white_to_black,
            }
            for region in self.correction_regions
        ]
        data["generated_same_neighbor_histogram"] = {
            str(support): count
            for support, count
            in self.generated_same_neighbor_histogram.items()
        }
        return data


def benchmark_reports_json(
    reports: Iterable[BenchmarkReport],
    *,
    indent: int | None = 2,
) -> str:
    """Serialize benchmark reports as stable, machine-readable JSON."""

    return json.dumps(
        [report.to_dict() for report in reports],
        indent=indent,
        sort_keys=True,
    )


def format_benchmark_reports(
    reports: Iterable[BenchmarkReport],
) -> str:
    """Format benchmark reports for command-line inspection."""

    sections = []
    for report in reports:
        project_name = report.project_path or "<in-memory project>"
        region_sizes = ", ".join(
            str(region.size) for region in report.correction_regions
        ) or "none"
        lines = [
            f"Project: {project_name}",
            f"Full tiles: {report.full_tiles}",
            f"Stored overrides: {report.stored_overrides}",
            f"Real changed overrides: {report.real_changed_overrides}",
            "Directional changes: "
            f"{report.black_to_white} black -> white, "
            f"{report.white_to_black} white -> black",
            "Black tiles: "
            f"{report.generated_black} generated, "
            f"{report.effective_black} effective",
            "Boundary edges: "
            f"{report.generated_boundary_edges} generated, "
            f"{report.effective_boundary_edges} effective",
            f"Correction region sizes: {region_sizes}",
            "Local topology: "
            f"{report.changed_with_opposite_majority} opposite-majority, "
            f"{report.changed_matching_cleanup_rule} cleanup-rule matches",
        ]
        if report.coverage_disagreements is None:
            lines.append("Source coverage disagreement: not computed")
        else:
            lines.append(
                "Source coverage disagreement: "
                f"{report.coverage_disagreements} total "
                f"({report.black_to_white_coverage_disagreements} "
                "black -> white, "
                f"{report.white_to_black_coverage_disagreements} "
                "white -> black)"
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _active_full(project: MosaicProject) -> set[Coordinate]:
    return {
        (placement.row, placement.column)
        for placement in project.geometry.placements
        if placement.piece_type == "full"
    }


def _neighbors(project: MosaicProject, coordinate: Coordinate, active):
    row, column = coordinate
    return [
        neighbor
        for neighbor in tile_neighbors(
            row,
            column,
            project.rows,
            project.columns,
            project.config,
        )
        if neighbor in active
    ]


def connected_correction_regions(
    project: MosaicProject,
) -> tuple[CorrectionRegion, ...]:
    """Group real changes using physical edge-sharing adjacency."""

    generated = project.generated_grid
    changes = {
        coordinate
        for coordinate, value in project.overrides.items()
        if generated[coordinate[0]][coordinate[1]] != value
    }
    active = _active_full(project)
    remaining = set(changes)
    regions = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        stack = [seed]
        coordinates = []
        while stack:
            coordinate = stack.pop()
            coordinates.append(coordinate)
            for neighbor in _neighbors(project, coordinate, active):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        black_to_white = sum(
            generated[row][column] == palette_extremes(project.palette)[0]
            for row, column in coordinates
        )
        regions.append(CorrectionRegion(
            coordinates=tuple(sorted(coordinates)),
            black_to_white=black_to_white,
            white_to_black=len(coordinates) - black_to_white,
        ))
    return tuple(sorted(regions, key=lambda region: (-region.size, region.coordinates)))


def _boundary_edges(project: MosaicProject, grid, active) -> int:
    edges = 0
    for coordinate in active:
        row, column = coordinate
        for nr, nc in _neighbors(project, coordinate, active):
            if coordinate < (nr, nc) and grid[row][column] != grid[nr][nc]:
                edges += 1
    return edges


def analyze_project(
    project: MosaicProject,
    *,
    evidence: BWEvidence | None = None,
    project_path: str | Path | None = None,
) -> BenchmarkReport:
    """Compare immutable generated assignments with human-effective state."""

    generated = project.generated_grid
    effective = project.effective_grid
    active = _active_full(project)
    dark, _ = palette_extremes(list(project.palette))
    changes = [
        (coordinate, generated[coordinate[0]][coordinate[1]], value)
        for coordinate, value in project.overrides.items()
        if generated[coordinate[0]][coordinate[1]] != value
    ]
    black_to_white = sum(old == dark for _, old, _ in changes)
    same_histogram: Counter[int] = Counter()
    opposite_majority = cleanup_matches = 0
    for coordinate, old, new in changes:
        neighbors = _neighbors(project, coordinate, active)
        same = sum(generated[r][c] == old for r, c in neighbors)
        opposite = sum(generated[r][c] == new for r, c in neighbors)
        same_histogram[same] += 1
        required = len(neighbors) // 2 + 1
        if opposite >= required:
            opposite_majority += 1
        if same <= 1 and opposite >= required:
            cleanup_matches += 1
    coverage_disagreements = bw_disagreements = wb_disagreements = None
    mean_coverage = None
    if evidence is not None:
        values = []
        bw_disagreements = wb_disagreements = 0
        for coordinate, old, _ in changes:
            tile = evidence.tiles.get(coordinate)
            if tile is None:
                continue
            values.append(tile.raw_coverage)
            classified_black = tile.raw_coverage >= evidence.coverage_threshold
            if old == dark and classified_black:
                bw_disagreements += 1
            elif old != dark and not classified_black:
                wb_disagreements += 1
        coverage_disagreements = bw_disagreements + wb_disagreements
        mean_coverage = sum(values) / len(values) if values else None
    return BenchmarkReport(
        project_path=str(project_path) if project_path is not None else None,
        full_tiles=len(active),
        stored_overrides=len(project.overrides),
        real_changed_overrides=len(changes),
        black_to_white=black_to_white,
        white_to_black=len(changes) - black_to_white,
        generated_black=sum(generated[r][c] == dark for r, c in active),
        effective_black=sum(effective[r][c] == dark for r, c in active),
        generated_boundary_edges=_boundary_edges(project, generated, active),
        effective_boundary_edges=_boundary_edges(project, effective, active),
        correction_regions=connected_correction_regions(project),
        changed_with_opposite_majority=opposite_majority,
        changed_matching_cleanup_rule=cleanup_matches,
        generated_same_neighbor_histogram=dict(sorted(same_histogram.items())),
        coverage_disagreements=coverage_disagreements,
        black_to_white_coverage_disagreements=bw_disagreements,
        white_to_black_coverage_disagreements=wb_disagreements,
        changed_mean_coverage=mean_coverage,
    )


def analyze_benchmark_projects(
    project_paths: Iterable[str | Path],
    *,
    evidence_by_path: dict[str | Path, BWEvidence] | None = None,
    compute_source_evidence: bool = False,
) -> tuple[BenchmarkReport, ...]:
    """Load and analyze any number of saved reference projects.

    Source files are not required unless compute_source_evidence is true.
    Explicit evidence_by_path entries take precedence over source loading.
    """

    evidence_by_path = evidence_by_path or {}
    reports = []
    for path_value in project_paths:
        path = Path(path_value)
        evidence = evidence_by_path.get(path_value, evidence_by_path.get(path))
        project = MosaicProject.load(path)
        if evidence is None and compute_source_evidence:
            evidence = compute_project_bw_evidence(project)
        reports.append(analyze_project(
            project, evidence=evidence, project_path=path
        ))
    return tuple(reports)

from __future__ import annotations

from dataclasses import asdict, dataclass
from heapq import heappop, heappush
from math import acos, exp, hypot

from .evidence import BWEvidence, Coordinate
from .processing import palette_extremes, tile_neighbors
from .project import MosaicProject


Point = tuple[float, float]


@dataclass(frozen=True)
class ContourScore:
    source_trajectory_agreement: float
    directional_smoothness: float
    apparent_stroke_width: float
    boundary_complexity: float
    topology_negative_space: float
    change_cost: float

    @property
    def total(self) -> float:
        return sum(asdict(self).values())

    def to_dict(self) -> dict:
        data = asdict(self)
        data["total"] = self.total
        return data


@dataclass(frozen=True)
class ContourChange:
    tile_id: str
    row: int
    column: int
    generated_index: int
    proposed_index: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ContourAlternative:
    name: str
    rank: int
    path: tuple[Coordinate, ...]
    proposed_contour: tuple[Point, ...]
    changes: tuple[ContourChange, ...]
    score: ContourScore
    score_delta: float
    is_recommended: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rank": self.rank,
            "path": [list(value) for value in self.path],
            "proposed_contour": [list(value) for value in self.proposed_contour],
            "changes": [change.to_dict() for change in self.changes],
            "score": self.score.to_dict(),
            "score_delta": self.score_delta,
            "is_recommended": self.is_recommended,
        }


@dataclass(frozen=True)
class ContourCandidate:
    candidate_id: str
    reason: str
    region: tuple[Coordinate, ...]
    affected_tile_ids: tuple[str, ...]
    source_contour: tuple[Point, ...]
    current_mosaic_contour: tuple[Point, ...]
    baseline_score: ContourScore
    alternatives: tuple[ContourAlternative, ...]
    recommended_alternative: str | None

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "region": [list(value) for value in self.region],
            "affected_tile_ids": list(self.affected_tile_ids),
            "source_contour": [list(value) for value in self.source_contour],
            "current_mosaic_contour": [
                list(value) for value in self.current_mosaic_contour
            ],
            "baseline_score": self.baseline_score.to_dict(),
            "alternatives": [value.to_dict() for value in self.alternatives],
            "recommended_alternative": self.recommended_alternative,
            "recommendation": (
                self.recommended_alternative
                if self.recommended_alternative is not None
                else "No recommended refinement"
            ),
        }


@dataclass(frozen=True)
class ContourRefinementReport:
    candidates: tuple[ContourCandidate, ...]

    def to_dict(self) -> dict:
        return {
            "experiment": "continuous-contour-v1",
            "candidates": [value.to_dict() for value in self.candidates],
        }


def _full(project: MosaicProject) -> set[Coordinate]:
    return {
        (value.row, value.column)
        for value in project.geometry.placements
        if value.piece_type == "full"
    }


def _neighbors(project, coordinate, active):
    return tuple(sorted(
        value
        for value in tile_neighbors(
            coordinate[0], coordinate[1], project.rows, project.columns,
            project.config,
        )
        if value in active
    ))


def _center(project, coordinate) -> Point:
    placement = project.geometry.placement(*coordinate)
    return placement.center_x_in, placement.center_y_in


def _tile_id(project, coordinate) -> str:
    return f"placement-{coordinate[0] * project.columns + coordinate[1]:06d}"


def _indices(project):
    dark, light = palette_extremes(list(project.palette))
    return (light, dark) if project.config.invert_bw else (dark, light)


def _boundary_tiles(project, active, foreground):
    grid = project.generated_grid
    return {
        coordinate
        for coordinate in active
        if grid[coordinate[0]][coordinate[1]] == foreground
        and any(
            grid[row][column] != foreground
            for row, column in _neighbors(project, coordinate, active)
        )
    }


def _components(project, coordinates, active):
    remaining = set(coordinates)
    result = []
    while remaining:
        queue = [min(remaining)]
        remaining.remove(queue[0])
        component = []
        while queue:
            coordinate = queue.pop(0)
            component.append(coordinate)
            for neighbor in _neighbors(project, coordinate, active):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        if len(component) >= 3:
            result.append(tuple(sorted(component)))
    return sorted(result)


def _distance(project, first, second):
    ax, ay = _center(project, first)
    bx, by = _center(project, second)
    return hypot(ax - bx, ay - by)


def _endpoints(project, coordinates):
    pairs = (
        (_distance(project, first, second), first, second)
        for index, first in enumerate(coordinates)
        for second in coordinates[index + 1:]
    )
    _, first, second = max(pairs, default=(0.0, coordinates[0], coordinates[-1]))
    return first, second


def _ordered_coordinates(project, coordinates, endpoints):
    start, end = endpoints
    sx, sy = _center(project, start)
    ex, ey = _center(project, end)
    dx, dy = ex - sx, ey - sy
    length = hypot(dx, dy) or 1.0
    return tuple(sorted(
        coordinates,
        key=lambda value: (
            ((_center(project, value)[0] - sx) * dx
             + (_center(project, value)[1] - sy) * dy) / length,
            value,
        ),
    ))


def _ordered_points(project, coordinates, endpoints):
    return tuple(
        _center(project, coordinate)
        for coordinate in _ordered_coordinates(project, coordinates, endpoints)
    )


def _path(
    project,
    evidence,
    region,
    start,
    end,
    mode,
):
    sx, sy = _center(project, start)
    ex, ey = _center(project, end)
    line_dx, line_dy = ex - sx, ey - sy
    line_length = hypot(line_dx, line_dy) or 1.0

    def node_cost(coordinate):
        tile = evidence.tiles[coordinate]
        x, y = _center(project, coordinate)
        line_distance = abs(line_dy * x - line_dx * y + ex * sy - ey * sx)
        line_distance /= line_length
        source_distance = tile.source_centerline_distance_in
        if source_distance is None:
            source_distance = line_distance + project.config.tile_width_in
        width = tile.local_stroke_width_in or project.config.tile_width_in
        if mode == "source-trajectory":
            return 2.5 * source_distance + 0.25 * line_distance
        if mode == "cadence-regularized":
            return 0.7 * source_distance + 2.0 * line_distance
        return source_distance + abs(width - project.config.tile_width_in)

    heap = [(0.0, start, (start,))]
    best = {start: 0.0}
    while heap:
        cost, current, path = heappop(heap)
        if cost != best.get(current):
            continue
        if current == end:
            return path
        for neighbor in _neighbors(project, current, region):
            if (
                neighbor not in {start, end}
                and evidence.tiles[neighbor].raw_coverage < 0.02
                and project.generated_grid[neighbor[0]][neighbor[1]]
                != _indices(project)[0]
            ):
                continue
            candidate = cost + _distance(project, current, neighbor)
            candidate += node_cost(neighbor)
            if candidate < best.get(neighbor, float("inf")):
                best[neighbor] = candidate
                heappush(heap, (candidate, neighbor, (*path, neighbor)))
    return ()


def _component_count(project, assignments, region, active, foreground):
    remaining = {value for value in region if assignments[value] == foreground}
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            for neighbor in _neighbors(project, stack.pop(), active):
                if neighbor in remaining and neighbor in region:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    return count


def _score(
    project,
    evidence,
    region,
    path,
    changes,
    active,
    foreground,
    baseline_components,
):
    grid = project.generated_grid
    assignments = {
        value: changes.get(value, grid[value[0]][value[1]])
        for value in region
    }
    scale = max(project.config.tile_width_in, 1e-9)
    source_distances = [
        evidence.tiles[value].source_centerline_distance_in
        for value in path
        if evidence.tiles[value].source_centerline_distance_in is not None
    ]
    trajectory = -sum(source_distances) / max(1, len(source_distances)) / scale
    turns = []
    for first, middle, last in zip(path, path[1:], path[2:]):
        ax, ay = _center(project, first)
        bx, by = _center(project, middle)
        cx, cy = _center(project, last)
        first_vector = (bx - ax, by - ay)
        second_vector = (cx - bx, cy - by)
        denominator = hypot(*first_vector) * hypot(*second_vector)
        if denominator:
            cosine = max(-1.0, min(1.0, (
                first_vector[0] * second_vector[0]
                + first_vector[1] * second_vector[1]
            ) / denominator))
            turns.append(acos(cosine))
    smoothness = -sum(turns) / max(1, len(turns))
    widths = []
    boundary_edges = total_edges = low_coverage_foreground = 0
    for coordinate in region:
        neighbors = [
            value for value in _neighbors(project, coordinate, active)
            if value in region
        ]
        foreground_neighbors = sum(
            assignments[value] == foreground for value in neighbors
        )
        tile = evidence.tiles[coordinate]
        if tile.local_stroke_width_in is not None and tile.raw_coverage > 0.05:
            mosaic_width = scale * (1.0 + foreground_neighbors / 3.0)
            widths.append(exp(-abs(
                mosaic_width - tile.local_stroke_width_in
            ) / scale))
        if assignments[coordinate] == foreground and tile.raw_coverage < 0.05:
            low_coverage_foreground += 1
        for neighbor in neighbors:
            if coordinate < neighbor:
                total_edges += 1
                boundary_edges += assignments[coordinate] != assignments[neighbor]
    width_score = sum(widths) / max(1, len(widths))
    complexity = -boundary_edges / max(1, total_edges)
    components = _component_count(
        project, assignments, region, active, foreground
    )
    topology = -2.0 * abs(components - baseline_components)
    topology -= low_coverage_foreground / max(1, len(region))
    return ContourScore(
        source_trajectory_agreement=3.0 * trajectory,
        directional_smoothness=1.5 * smoothness,
        apparent_stroke_width=width_score,
        boundary_complexity=complexity,
        topology_negative_space=topology,
        change_cost=-1.25 * len(changes) / max(1, len(region)),
    )


def generate_contour_refinement_proposals(
    project: MosaicProject,
    evidence: BWEvidence,
    *,
    max_segment_tiles: int = 20,
) -> ContourRefinementReport:
    """Build deterministic continuous-contour alternatives, read-only."""

    if project.config.quantization_mode != "bw":
        raise ValueError("Contour refinement currently requires BW mode.")
    active = _full(project)
    if not active.issubset(evidence.tiles):
        raise ValueError("Evidence is missing full tile placements.")
    foreground, background = _indices(project)
    boundary = _boundary_tiles(project, active, foreground)
    source_boundary = {
        coordinate
        for coordinate in active
        if evidence.tiles[coordinate].source_boundary_orientation_deg is not None
        and (
            0.02 < evidence.tiles[coordinate].raw_coverage < 0.98
            or evidence.tiles[coordinate].source_centerline_distance_in
            is not None
        )
    }
    seeds = boundary & source_boundary
    components = _components(project, seeds, active)
    segments = [
        tuple(component[start:start + max_segment_tiles])
        for component in components
        for start in range(0, len(component), max_segment_tiles)
        if len(component[start:start + max_segment_tiles]) >= 3
    ]
    candidates = []
    grid = project.generated_grid
    for index, segment in enumerate(segments, 1):
        region = set(segment)
        for coordinate in segment:
            region.update(_neighbors(project, coordinate, active))
        endpoints = _endpoints(project, segment)
        source_contour = _ordered_points(project, segment, endpoints)
        current_tiles = tuple(
            value for value in region if value in boundary
        )
        current_path = _path(
            project,
            evidence,
            set(current_tiles or segment),
            endpoints[0],
            endpoints[1],
            "cadence-regularized",
        )
        if not current_path:
            continue
        current_contour = tuple(
            _center(project, value) for value in current_path
        )
        baseline_assignments = {
            value: grid[value[0]][value[1]] for value in region
        }
        baseline_components = _component_count(
            project, baseline_assignments, region, active, foreground
        )
        baseline_path = current_path
        baseline = _score(
            project, evidence, region, baseline_path, {}, active,
            foreground, baseline_components,
        )
        raw_alternatives = []
        seen_paths = set()
        for name in (
            "source-trajectory",
            "cadence-regularized",
            "width-balanced",
        ):
            path = _path(
                project, evidence, region, endpoints[0], endpoints[1], name
            )
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            path_set = set(path)
            changes = {}
            for coordinate in path:
                if grid[coordinate[0]][coordinate[1]] != foreground:
                    changes[coordinate] = foreground
            for coordinate in current_tiles:
                foreground_neighbors = sum(
                    grid[row][column] == foreground
                    for row, column in _neighbors(project, coordinate, active)
                )
                if (
                    coordinate not in path_set
                    and foreground_neighbors <= 3
                    and evidence.tiles[coordinate].raw_coverage < 0.75
                ):
                    changes[coordinate] = background
            score = _score(
                project, evidence, region, path, changes, active,
                foreground, baseline_components,
            )
            contour_changes = tuple(
                ContourChange(
                    tile_id=_tile_id(project, coordinate),
                    row=coordinate[0],
                    column=coordinate[1],
                    generated_index=grid[coordinate[0]][coordinate[1]],
                    proposed_index=value,
                )
                for coordinate, value in sorted(changes.items())
            )
            raw_alternatives.append((name, path, contour_changes, score))
        raw_alternatives.sort(key=lambda value: (
            -(value[3].total - baseline.total), value[0], value[1]
        ))
        recommended = next((
            name for name, _, _, score in raw_alternatives
            if score.total > baseline.total
        ), None)
        alternatives = tuple(
            ContourAlternative(
                name=name,
                rank=rank,
                path=path,
                proposed_contour=tuple(_center(project, value) for value in path),
                changes=changes,
                score=score,
                score_delta=score.total - baseline.total,
                is_recommended=name == recommended,
            )
            for rank, (name, path, changes, score)
            in enumerate(raw_alternatives, 1)
        )
        reasons = (
            "coherent source and mosaic contours diverge across multiple "
            "physical neighbors; evaluate trajectory, cadence, lattice phase, "
            "and apparent width jointly"
        )
        if not alternatives:
            continue
        candidates.append(ContourCandidate(
            candidate_id=f"contour-{index:04d}",
            reason=reasons,
            region=tuple(sorted(region)),
            affected_tile_ids=tuple(
                _tile_id(project, value) for value in sorted(region)
            ),
            source_contour=source_contour,
            current_mosaic_contour=current_contour,
            baseline_score=baseline,
            alternatives=alternatives,
            recommended_alternative=recommended,
        ))
    return ContourRefinementReport(candidates=tuple(candidates))


def format_contour_refinement_report(report: ContourRefinementReport) -> str:
    lines = [f"Contour candidates: {len(report.candidates)}"]
    for candidate in report.candidates:
        lines.extend((
            "",
            f"{candidate.candidate_id}: {len(candidate.region)} tiles",
            f"  Why: {candidate.reason}",
            "  Recommendation: " + (
                candidate.recommended_alternative
                or "No recommended refinement"
            ),
        ))
        for alternative in candidate.alternatives:
            lines.append(
                f"  #{alternative.rank} {alternative.name}: "
                f"{len(alternative.changes)} changes, "
                f"delta {alternative.score_delta:+.4f}"
            )
    return "\n".join(lines)

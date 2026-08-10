from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp

from .evidence import BWEvidence, Coordinate
from .processing import palette_extremes, tile_neighbors
from .project import MosaicProject


@dataclass(frozen=True)
class ScoreBreakdown:
    source_agreement: float
    directional_continuity: float
    stroke_width_consistency: float
    boundary_regularity: float
    negative_space_preservation: float
    minimal_change: float
    topology_preservation: float

    @property
    def total(self) -> float:
        return sum(asdict(self).values())

    def to_dict(self) -> dict:
        data = asdict(self)
        data["total"] = self.total
        return data


@dataclass(frozen=True)
class TileChange:
    tile_id: str
    row: int
    column: int
    generated_index: int
    proposed_index: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CandidateRegion:
    candidate_id: str
    coordinates: tuple[Coordinate, ...]
    tile_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    alternatives: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "coordinates": [list(value) for value in self.coordinates],
            "tile_ids": list(self.tile_ids),
            "reasons": list(self.reasons),
            "alternatives": list(self.alternatives),
        }


@dataclass(frozen=True)
class RefinementProposal:
    candidate_id: str
    rank: int
    alternative: str
    affected_tile_ids: tuple[str, ...]
    changes: tuple[TileChange, ...]
    baseline_score: float
    alternative_score: float
    baseline_breakdown: ScoreBreakdown
    alternative_breakdown: ScoreBreakdown
    reason: str

    @property
    def score_delta(self) -> float:
        return self.alternative_score - self.baseline_score

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "alternative": self.alternative,
            "affected_tile_ids": list(self.affected_tile_ids),
            "changes": [change.to_dict() for change in self.changes],
            "baseline_score": self.baseline_score,
            "alternative_score": self.alternative_score,
            "score_delta": self.score_delta,
            "baseline_breakdown": self.baseline_breakdown.to_dict(),
            "alternative_breakdown": self.alternative_breakdown.to_dict(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RefinementReport:
    candidates: tuple[CandidateRegion, ...]
    proposals: tuple[RefinementProposal, ...]

    def to_dict(self) -> dict:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "proposals": [proposal.to_dict() for proposal in self.proposals],
        }


def _tile_id(project: MosaicProject, coordinate: Coordinate) -> str:
    row, column = coordinate
    return f"placement-{row * project.columns + column:06d}"


def _full_tiles(project: MosaicProject) -> set[Coordinate]:
    return {
        (placement.row, placement.column)
        for placement in project.geometry.placements
        if placement.piece_type == "full"
    }


def _neighbors(
    project: MosaicProject,
    coordinate: Coordinate,
    full: set[Coordinate],
) -> tuple[Coordinate, ...]:
    return tuple(sorted(
        neighbor
        for neighbor in tile_neighbors(
            coordinate[0],
            coordinate[1],
            project.rows,
            project.columns,
            project.config,
        )
        if neighbor in full
    ))


def _foreground_indices(project: MosaicProject) -> tuple[int, int]:
    dark, light = palette_extremes(list(project.palette))
    if project.config.invert_bw:
        return light, dark
    return dark, light


def _seed_reasons(
    project: MosaicProject,
    evidence: BWEvidence,
    coordinate: Coordinate,
    full: set[Coordinate],
    foreground: int,
) -> tuple[str, ...]:
    row, column = coordinate
    generated = project.generated_grid
    current = generated[row][column]
    neighbors = _neighbors(project, coordinate, full)
    same = sum(generated[r][c] == current for r, c in neighbors)
    opposite = len(neighbors) - same
    tile = evidence.tiles[coordinate]
    reasons = []
    if same <= 1 and opposite >= len(neighbors) // 2 + 1:
        reasons.append("strong local notch or protrusion topology")
    boundary = bool(neighbors) and 0 < same < len(neighbors)
    if boundary and same <= 3 and tile.source_boundary_orientation_deg is not None:
        reasons.append("irregular source-aligned stroke boundary")
    tile_scale = max(
        project.config.tile_width_in,
        project.config.tile_height_in,
    )
    if (
        current != foreground
        and tile.raw_coverage < evidence.coverage_threshold
        and tile.source_centerline_distance_in is not None
        and tile.local_stroke_width_in is not None
        and tile.source_centerline_distance_in
        <= max(0.75 * tile_scale, 0.75 * tile.local_stroke_width_in)
    ):
        reasons.append("subthreshold source stroke crosses consecutive tiles")
    if (
        boundary
        and tile.local_stroke_width_in is not None
        and tile.local_stroke_width_in >= 0.35 * tile_scale
        and same in {2, 3}
    ):
        reasons.append("locally inconsistent apparent stroke width")
    return tuple(reasons)


def _split_components(
    project: MosaicProject,
    seeds: set[Coordinate],
    full: set[Coordinate],
    max_seeds: int,
) -> list[tuple[Coordinate, ...]]:
    remaining = set(seeds)
    chunks = []
    while remaining:
        queue = [min(remaining)]
        remaining.remove(queue[0])
        component = []
        while queue:
            coordinate = queue.pop(0)
            component.append(coordinate)
            for neighbor in _neighbors(project, coordinate, full):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        for start in range(0, len(component), max_seeds):
            chunks.append(tuple(sorted(component[start:start + max_seeds])))
    return sorted(chunks)


def _region_coordinates(
    project: MosaicProject,
    seeds: tuple[Coordinate, ...],
    full: set[Coordinate],
) -> tuple[Coordinate, ...]:
    region = set(seeds)
    for coordinate in seeds:
        region.update(_neighbors(project, coordinate, full))
    return tuple(sorted(region))


def _change_sets(
    project: MosaicProject,
    evidence: BWEvidence,
    coordinates: tuple[Coordinate, ...],
    full: set[Coordinate],
    foreground: int,
    background: int,
) -> dict[str, dict[Coordinate, int]]:
    generated = project.generated_grid
    threshold = evidence.coverage_threshold
    tile_scale = max(
        project.config.tile_width_in,
        project.config.tile_height_in,
    )
    expand = []
    contract = []
    notch = []
    for coordinate in coordinates:
        row, column = coordinate
        value = generated[row][column]
        neighbors = _neighbors(project, coordinate, full)
        foreground_neighbors = sum(
            generated[r][c] == foreground for r, c in neighbors
        )
        tile = evidence.tiles[coordinate]
        if value == background and foreground_neighbors:
            centerline_support = (
                tile.source_centerline_distance_in is not None
                and tile.source_centerline_distance_in
                <= max(
                    0.75 * tile_scale,
                    0.75 * (tile.local_stroke_width_in or 0.0),
                )
            )
            if tile.raw_coverage >= threshold - 0.16 or centerline_support:
                expand.append(coordinate)
        if value == foreground and foreground_neighbors < len(neighbors):
            if tile.raw_coverage <= threshold + 0.16 or foreground_neighbors <= 2:
                contract.append(coordinate)
        same = sum(generated[r][c] == value for r, c in neighbors)
        opposite = len(neighbors) - same
        if same <= 1 and opposite >= len(neighbors) // 2 + 1:
            notch.append(coordinate)

    expand.sort(key=lambda c: (
        evidence.tiles[c].source_centerline_distance_in
        if evidence.tiles[c].source_centerline_distance_in is not None
        else float("inf"),
        -evidence.tiles[c].raw_coverage,
        c,
    ))
    contract.sort(key=lambda c: (evidence.tiles[c].raw_coverage, c))
    alternatives: dict[str, dict[Coordinate, int]] = {}
    if expand:
        alternatives["expand"] = {c: foreground for c in expand[:8]}
    if contract:
        alternatives["contract"] = {c: background for c in contract[:8]}
    if expand and contract:
        pair_count = min(6, len(expand), len(contract))
        alternatives["shift"] = {
            **{c: foreground for c in expand[:pair_count]},
            **{c: background for c in contract[:pair_count]},
        }
    if notch:
        alternatives["notch_cleanup"] = {
            c: (
                foreground
                if generated[c[0]][c[1]] == background
                else background
            )
            for c in notch[:4]
        }
    return alternatives


def _component_count(
    project: MosaicProject,
    assignments: dict[Coordinate, int],
    coordinates: set[Coordinate],
    full: set[Coordinate],
    foreground: int,
) -> int:
    remaining = {
        coordinate
        for coordinate in coordinates
        if assignments[coordinate] == foreground
    }
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            for neighbor in _neighbors(project, stack.pop(), full):
                if neighbor in remaining and neighbor in coordinates:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    return count


def _score(
    project: MosaicProject,
    evidence: BWEvidence,
    evaluation: set[Coordinate],
    changes: dict[Coordinate, int],
    full: set[Coordinate],
    foreground: int,
    background: int,
    baseline_components: int,
) -> ScoreBreakdown:
    generated = project.generated_grid
    assignments = {
        coordinate: changes.get(
            coordinate, generated[coordinate[0]][coordinate[1]]
        )
        for coordinate in evaluation
    }
    count = max(1, len(evaluation))
    agreement = 0.0
    negative_space = 0.0
    continuity = 0.0
    width_scores = []
    boundary_edges = total_edges = 0
    tile_scale = max(
        project.config.tile_width_in,
        project.config.tile_height_in,
    )
    for coordinate in evaluation:
        tile = evidence.tiles[coordinate]
        value = assignments[coordinate]
        agreement += (
            tile.raw_coverage
            if value == foreground
            else 1.0 - tile.raw_coverage
        )
        if tile.raw_coverage <= 0.15:
            negative_space += 1.0 if value == background else -1.0
        neighbors = [
            neighbor
            for neighbor in _neighbors(project, coordinate, full)
            if neighbor in evaluation
        ]
        foreground_neighbors = sum(
            assignments[neighbor] == foreground for neighbor in neighbors
        )
        if value == foreground:
            continuity += min(1.0, foreground_neighbors / 2.0)
        if (
            tile.local_stroke_width_in is not None
            and (
                tile.source_foreground_at_center
                or tile.raw_coverage > 0.1
            )
        ):
            mosaic_width = tile_scale * (1.0 + foreground_neighbors / 3.0)
            difference = abs(mosaic_width - tile.local_stroke_width_in)
            width_scores.append(exp(-difference / max(tile_scale, 1e-9)))
        for neighbor in neighbors:
            if coordinate < neighbor:
                total_edges += 1
                if value != assignments[neighbor]:
                    boundary_edges += 1
    components = _component_count(
        project, assignments, evaluation, full, foreground
    )
    return ScoreBreakdown(
        source_agreement=4.0 * agreement / count,
        directional_continuity=continuity / count,
        stroke_width_consistency=(
            sum(width_scores) / len(width_scores) if width_scores else 0.0
        ),
        boundary_regularity=(
            -boundary_edges / total_edges if total_edges else 0.0
        ),
        negative_space_preservation=2.0 * negative_space / count,
        minimal_change=-1.5 * len(changes) / count,
        topology_preservation=-2.0 * abs(components - baseline_components),
    )


def generate_refinement_proposals(
    project: MosaicProject,
    evidence: BWEvidence,
    *,
    max_region_seeds: int = 12,
) -> RefinementReport:
    """Generate deterministic, read-only regional refinement proposals."""

    if project.config.quantization_mode != "bw":
        raise ValueError("Refinement proposals currently require BW mode.")
    full = _full_tiles(project)
    if not full.issubset(evidence.tiles):
        raise ValueError("Evidence is missing one or more full tile placements.")
    foreground, background = _foreground_indices(project)
    seed_reasons = {
        coordinate: _seed_reasons(
            project, evidence, coordinate, full, foreground
        )
        for coordinate in sorted(full)
    }
    seeds = {coordinate for coordinate, reasons in seed_reasons.items() if reasons}
    chunks = _split_components(
        project, seeds, full, max(1, max_region_seeds)
    )
    candidates = []
    proposal_rows = []
    for index, chunk in enumerate(chunks, 1):
        candidate_id = f"region-{index:04d}"
        coordinates = _region_coordinates(project, chunk, full)
        reasons = tuple(sorted({
            reason
            for coordinate in chunk
            for reason in seed_reasons[coordinate]
        }))
        change_sets = _change_sets(
            project,
            evidence,
            coordinates,
            full,
            foreground,
            background,
        )
        if "expand" in change_sets and "contract" in change_sets:
            reasons = tuple(sorted(set(reasons) | {
                "paired opposing edges permit a one-phase shift"
            }))
        candidates.append(CandidateRegion(
            candidate_id=candidate_id,
            coordinates=coordinates,
            tile_ids=tuple(_tile_id(project, c) for c in coordinates),
            reasons=reasons,
            alternatives=("retain", *sorted(change_sets)),
        ))
        evaluation = set(coordinates)
        for coordinate in coordinates:
            evaluation.update(_neighbors(project, coordinate, full))
        baseline_assignments = {
            coordinate: project.generated_grid[coordinate[0]][coordinate[1]]
            for coordinate in evaluation
        }
        baseline_components = _component_count(
            project,
            baseline_assignments,
            evaluation,
            full,
            foreground,
        )
        baseline = _score(
            project,
            evidence,
            evaluation,
            {},
            full,
            foreground,
            background,
            baseline_components,
        )
        alternatives = []
        for name, proposed in sorted(change_sets.items()):
            actual = {
                coordinate: value
                for coordinate, value in proposed.items()
                if project.generated_grid[coordinate[0]][coordinate[1]] != value
            }
            if not actual:
                continue
            score = _score(
                project,
                evidence,
                evaluation,
                actual,
                full,
                foreground,
                background,
                baseline_components,
            )
            changes = tuple(
                TileChange(
                    tile_id=_tile_id(project, coordinate),
                    row=coordinate[0],
                    column=coordinate[1],
                    generated_index=project.generated_grid[
                        coordinate[0]
                    ][coordinate[1]],
                    proposed_index=value,
                )
                for coordinate, value in sorted(actual.items())
            )
            alternatives.append((name, changes, score))
        alternatives.sort(key=lambda item: (
            -(item[2].total - baseline.total), item[0],
            tuple((change.row, change.column) for change in item[1]),
        ))
        for rank, (name, changes, score) in enumerate(alternatives, 1):
            proposal_rows.append(RefinementProposal(
                candidate_id=candidate_id,
                rank=rank,
                alternative=name,
                affected_tile_ids=tuple(change.tile_id for change in changes),
                changes=changes,
                baseline_score=baseline.total,
                alternative_score=score.total,
                baseline_breakdown=baseline,
                alternative_breakdown=score,
                reason="; ".join(reasons),
            ))
    return RefinementReport(
        candidates=tuple(candidates),
        proposals=tuple(proposal_rows),
    )


def format_refinement_report(report: RefinementReport) -> str:
    lines = [
        f"Candidate regions: {len(report.candidates)}",
        f"Regional alternatives: {len(report.proposals)}",
    ]
    for candidate in report.candidates:
        lines.append("")
        lines.append(
            f"{candidate.candidate_id}: {len(candidate.coordinates)} tiles"
        )
        lines.append(f"  Why: {'; '.join(candidate.reasons)}")
        for proposal in (
            value for value in report.proposals
            if value.candidate_id == candidate.candidate_id
        ):
            lines.append(
                f"  #{proposal.rank} {proposal.alternative}: "
                f"{len(proposal.changes)} changes, "
                f"score {proposal.baseline_score:.4f} -> "
                f"{proposal.alternative_score:.4f} "
                f"({proposal.score_delta:+.4f})"
            )
            lines.append(
                "    Tiles: " + ", ".join(proposal.affected_tile_ids)
            )
            lines.append(
                "    Changes: "
                + ", ".join(
                    f"{change.tile_id} "
                    f"{change.generated_index}->{change.proposed_index}"
                    for change in proposal.changes
                )
            )
            baseline = proposal.baseline_breakdown
            alternative = proposal.alternative_breakdown
            lines.append(
                "    Components (baseline -> alternative): "
                f"source {baseline.source_agreement:.3f} -> "
                f"{alternative.source_agreement:.3f}; "
                f"continuity {baseline.directional_continuity:.3f} -> "
                f"{alternative.directional_continuity:.3f}; "
                f"width {baseline.stroke_width_consistency:.3f} -> "
                f"{alternative.stroke_width_consistency:.3f}; "
                f"boundary {baseline.boundary_regularity:.3f} -> "
                f"{alternative.boundary_regularity:.3f}; "
                f"negative-space {baseline.negative_space_preservation:.3f} "
                f"-> {alternative.negative_space_preservation:.3f}; "
                f"minimal-change {baseline.minimal_change:.3f} -> "
                f"{alternative.minimal_change:.3f}; "
                f"topology {baseline.topology_preservation:.3f} -> "
                f"{alternative.topology_preservation:.3f}"
            )
    return "\n".join(lines)

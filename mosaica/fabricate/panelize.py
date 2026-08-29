from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
from math import ceil, sqrt
from pathlib import Path

from .. import __version__
from ..project import MosaicProject
from .export import parse_ascii_stl, write_mesh_stl
from .mesh import (
    MeshBody, SinglePanelGeometry, concave_grout_mesh,
    debossed_cell_union_base_mesh, fabrication_perimeter_bounds,
    mesh_validation,
)
from .modes import FabricationMode, resolve_fabrication_mode
from .model import Point2MM, ResolvedFabricationModel, ResolvedTile
from .phase2a import _panel_tile_body
from .phase2b import (
    PANEL_ID_CELL_MM, PANEL_ID_DEBOSS_DEPTH_MM, PRODUCTION_PROFILE,
    PanelIdentity, _marking_cells_at, _marking_dimensions, panel_identifier,
)
from .resolve import resolve_mosaic_project


# Compatibility alias for the former single production envelope. New work
# resolves its envelope from FabricationMode before panelization.
P1S_V1_SAFE_ENVELOPE_MM = (210.0, 210.0)
PANELIZATION_SCHEMA = "mosaica-fabricate-panelization"
PANELIZATION_SCHEMA_VERSION = 2


class PanelizationError(ValueError):
    pass


@dataclass(frozen=True)
class PanelPlan:
    panel_id: str
    row: int
    column: int
    tile_ids: tuple[str, ...]
    bounds_mm: tuple[float, float, float, float]
    area_mm2: float
    neighbors: tuple[tuple[str, str], ...]
    print_rotation_degrees: int = 0
    fits_safe_envelope: bool = True

    @property
    def width_mm(self) -> float:
        return round(self.bounds_mm[2] - self.bounds_mm[0], 9)

    @property
    def height_mm(self) -> float:
        return round(self.bounds_mm[3] - self.bounds_mm[1], 9)


@dataclass(frozen=True)
class PanelizationPlan:
    model: ResolvedFabricationModel
    fabrication_mode: FabricationMode
    safe_envelope_mm: tuple[float, float]
    theoretical_rows: int
    theoretical_columns: int
    rows: int
    columns: int
    x_cuts_mm: tuple[float, ...]
    y_cuts_mm: tuple[float, ...]
    panels: tuple[PanelPlan, ...]
    tile_ownership: tuple[tuple[str, str], ...]
    score: tuple[float | int, ...]
    attempted_layouts: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class PanelizedFabrication:
    plan: PanelizationPlan
    panels: tuple[SinglePanelGeometry, ...]
    marking_cells: tuple[tuple[str, tuple[tuple[Point2MM, ...], ...]], ...]


@dataclass(frozen=True)
class PanelizationPackage:
    output_directory: Path
    manifest_path: Path
    stl_paths: tuple[Path, ...]
    geometry_signature: str


def front_view_bounds(
    model: ResolvedFabricationModel,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Convert export geometry bounds to finished-front artwork coordinates."""

    left, top, right, bottom = bounds
    return (
        round(model.artwork_width_mm - right, 9), top,
        round(model.artwork_width_mm - left, 9), bottom,
    )


def _export_body_x(
    body: MeshBody, artwork_width_mm: float,
) -> MeshBody:
    """Convert a validated front-view body into backside-down export space."""

    triangles = tuple(
        tuple(
            (round(artwork_width_mm - x, 9), y, z)
            for x, y, z in (triangle[0], triangle[2], triangle[1])
        )
        for triangle in body.triangles
    )
    return MeshBody(
        body.body_id, body.name, body.material_channel_id, triangles,
        body.tile_ids, body.solid_triangle_counts,
    )


def theoretical_grid_counts(
    width_mm: float, height_mm: float,
    safe_envelope_mm: tuple[float, float] | None = None,
) -> tuple[int, int]:
    if width_mm <= 0 or height_mm <= 0:
        raise ValueError("Fabricated dimensions must be positive.")
    safe_width, safe_height = (
        resolve_fabrication_mode().safe_envelope_mm
        if safe_envelope_mm is None else safe_envelope_mm
    )
    if safe_width <= 0 or safe_height <= 0:
        raise ValueError("Panel safe-envelope dimensions must be positive.")
    return ceil(height_mm / safe_height), ceil(width_mm / safe_width)


def _expanded_parent_cell(
    model: ResolvedFabricationModel, tile: ResolvedTile,
) -> tuple[Point2MM, ...]:
    ratio = (
        model.tile_flat_to_flat_mm + model.grout_gap_mm
    ) / model.tile_flat_to_flat_mm
    center_x, center_y = tile.center_mm
    return tuple((
        round(center_x + (x - center_x) * ratio, 6),
        round(center_y + (y - center_y) * ratio, 6),
    ) for x, y in tile.full_polygon_mm)


def _area(polygon: tuple[Point2MM, ...]) -> float:
    return abs(0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(polygon, polygon[1:] + polygon[:1])
    ))


def _clip_polygon(
    polygon: tuple[Point2MM, ...],
    bounds: tuple[float, float, float, float],
) -> tuple[Point2MM, ...]:
    def clip(axis: int, plane: float, keep_greater: bool) -> None:
        nonlocal polygon

        def inside(point: Point2MM) -> bool:
            return point[axis] >= plane - 1e-9 if keep_greater else point[axis] <= plane + 1e-9

        result = []
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            first_inside, second_inside = inside(first), inside(second)
            if first_inside:
                result.append(first)
            if first_inside != second_inside:
                scale = (plane - first[axis]) / (second[axis] - first[axis])
                result.append(tuple(
                    plane if index == axis else round(
                        first[index] + scale * (second[index] - first[index]), 9,
                    )
                    for index in range(2)
                ))
        polygon = tuple(result)

    for values in ((0, bounds[0], True), (0, bounds[2], False),
                   (1, bounds[1], True), (1, bounds[3], False)):
        if polygon:
            clip(*values)
    return polygon if len(polygon) >= 3 and _area(polygon) > 1e-9 else ()


def _edge_key(first: Point2MM, second: Point2MM) -> tuple[Point2MM, Point2MM]:
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def _lattice_topology(
    model: ResolvedFabricationModel,
) -> tuple[
    dict[str, tuple[Point2MM, ...]],
    dict[str, set[str]],
    dict[tuple[Point2MM, Point2MM], tuple[str, ...]],
]:
    cells = {tile.tile_id: _expanded_parent_cell(model, tile) for tile in model.tiles}
    owners: dict[tuple[Point2MM, Point2MM], list[str]] = {}
    for tile_id, polygon in cells.items():
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            owners.setdefault(_edge_key(first, second), []).append(tile_id)
    adjacency = {tile.tile_id: set() for tile in model.tiles}
    for tile_ids in owners.values():
        if len(tile_ids) == 2:
            adjacency[tile_ids[0]].add(tile_ids[1])
            adjacency[tile_ids[1]].add(tile_ids[0])
    return cells, adjacency, {edge: tuple(values) for edge, values in owners.items()}


def _candidate_cut_sets(
    coordinates: tuple[float, ...], count: int,
    targets: tuple[float, ...], limit: int = 2048,
) -> tuple[tuple[float, ...], ...]:
    if count == 1:
        return ((),)
    gaps = tuple(
        round((first + second) / 2.0, 9)
        for first, second in zip(coordinates, coordinates[1:])
        if second - first > 1e-8
    )
    if len(gaps) < count - 1:
        return ()
    beam: list[tuple[float, ...]] = [()]
    for index, target in enumerate(targets):
        remaining = len(targets) - index - 1
        candidates = [
            prefix + (gap,)
            for prefix in beam
            for gap_index, gap in enumerate(gaps)
            if (not prefix or gap > prefix[-1])
            and len(gaps) - gap_index - 1 >= remaining
        ]
        beam = sorted(
            set(candidates),
            key=lambda cuts: (
                round(sum(
                    abs(value - expected)
                    for value, expected in zip(cuts, targets[:len(cuts)])
                ), 9),
                cuts,
            ),
        )[:limit]
    return tuple(beam)


def _axis_bounds_fit(
    model: ResolvedFabricationModel,
    cells: dict[str, tuple[Point2MM, ...]],
    cuts: tuple[float, ...],
    axis: int,
    safe_size_mm: float,
) -> bool:
    fabrication_bounds = fabrication_perimeter_bounds(model)
    grouped: dict[int, list[tuple[float, float]]] = {
        index: [] for index in range(len(cuts) + 1)
    }
    lower_bound = fabrication_bounds[axis]
    upper_bound = fabrication_bounds[axis + 2]
    for tile in model.tiles:
        band = bisect_right(cuts, tile.center_mm[axis])
        polygon = cells[tile.tile_id]
        grouped[band].append((
            max(lower_bound, min(point[axis] for point in polygon)),
            min(upper_bound, max(point[axis] for point in polygon)),
        ))
    return all(
        values and max(value[1] for value in values) - min(value[0] for value in values)
        <= safe_size_mm + 1e-7
        for values in grouped.values()
    )


def _is_contiguous(tile_ids: tuple[str, ...], adjacency: dict[str, set[str]]) -> bool:
    if not tile_ids:
        return False
    allowed = set(tile_ids)
    visited = set()
    pending = [tile_ids[0]]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current] & allowed - visited)
    return visited == allowed


def _boundary_loop_count(
    tile_ids: tuple[str, ...],
    cells: dict[str, tuple[Point2MM, ...]],
) -> int:
    counts: dict[tuple[Point2MM, Point2MM], int] = {}
    for tile_id in tile_ids:
        polygon = cells[tile_id]
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            edge = _edge_key(first, second)
            counts[edge] = counts.get(edge, 0) + 1
    adjacency: dict[Point2MM, set[Point2MM]] = {}
    for (first, second), count in counts.items():
        if count != 1:
            continue
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    if not adjacency or any(len(values) != 2 for values in adjacency.values()):
        return 0
    loops = 0
    unseen = set(adjacency)
    while unseen:
        loops += 1
        pending = [next(iter(unseen))]
        component = set()
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(adjacency[current] - component)
        unseen -= component
    return loops


def _panel_measurements(
    tile_ids: tuple[str, ...],
    cells: dict[str, tuple[Point2MM, ...]],
    fabrication_bounds: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float], float]:
    polygons = tuple(
        polygon for tile_id in tile_ids
        if (polygon := _clip_polygon(cells[tile_id], fabrication_bounds))
    )
    points = [point for polygon in polygons for point in polygon]
    return (
        min(point[0] for point in points), min(point[1] for point in points),
        max(point[0] for point in points), max(point[1] for point in points),
    ), round(sum(_area(polygon) for polygon in polygons), 9)


def _seam_complexity(
    ownership: dict[str, str],
    edge_owners: dict[tuple[Point2MM, Point2MM], tuple[str, ...]],
) -> tuple[int, int, float]:
    seams = [
        edge for edge, tile_ids in edge_owners.items()
        if len(tile_ids) == 2 and ownership[tile_ids[0]] != ownership[tile_ids[1]]
    ]
    adjacency: dict[Point2MM, list[Point2MM]] = {}
    length = 0.0
    for first, second in seams:
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
        length += sqrt((second[0] - first[0]) ** 2 + (second[1] - first[1]) ** 2)
    turns = 0
    for origin, neighbors in adjacency.items():
        if len(neighbors) != 2:
            continue
        first, second = neighbors
        cross = (
            (first[0] - origin[0]) * (second[1] - origin[1])
            - (first[1] - origin[1]) * (second[0] - origin[0])
        )
        if abs(cross) > 1e-7:
            turns += 1
    return turns, len(adjacency), round(length, 9)


def _evaluate_candidate(
    model: ResolvedFabricationModel,
    rows: int,
    columns: int,
    x_cuts: tuple[float, ...],
    y_cuts: tuple[float, ...],
    cells: dict[str, tuple[Point2MM, ...]],
    adjacency: dict[str, set[str]],
    edge_owners: dict[tuple[Point2MM, Point2MM], tuple[str, ...]],
    safe_envelope: tuple[float, float],
    targets_x: tuple[float, ...],
    targets_y: tuple[float, ...],
) -> tuple[tuple[PanelPlan, ...], dict[str, str], tuple[float | int, ...]] | None:
    grouped: dict[tuple[int, int], list[str]] = {
        (row, column): [] for row in range(rows) for column in range(columns)
    }
    tile_by_id = {tile.tile_id: tile for tile in model.tiles}
    ownership = {}
    for tile in model.tiles:
        row = bisect_right(y_cuts, tile.center_mm[1])
        column = bisect_right(x_cuts, tile.center_mm[0])
        panel_id = panel_identifier(row, column)
        grouped[row, column].append(tile.tile_id)
        ownership[tile.tile_id] = panel_id
    if any(not values for values in grouped.values()):
        return None

    fabrication_bounds = fabrication_perimeter_bounds(model)
    measured = {}
    for key, values in grouped.items():
        tile_ids = tuple(sorted(values, key=lambda tile_id: (
            tile_by_id[tile_id].center_mm[1], tile_by_id[tile_id].center_mm[0], tile_id,
        )))
        if not _is_contiguous(tile_ids, adjacency) or _boundary_loop_count(tile_ids, cells) != 1:
            return None
        bounds, area = _panel_measurements(tile_ids, cells, fabrication_bounds)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if width > safe_envelope[0] + 1e-7 or height > safe_envelope[1] + 1e-7:
            return None
        measured[key] = tile_ids, bounds, area

    panels = []
    for row in range(rows):
        for column in range(columns):
            panel_id = panel_identifier(row, column)
            neighbors = []
            for direction, neighbor_row, neighbor_column in (
                ("top", row - 1, column), ("bottom", row + 1, column),
                ("left", row, column - 1), ("right", row, column + 1),
            ):
                if 0 <= neighbor_row < rows and 0 <= neighbor_column < columns:
                    neighbors.append((direction, panel_identifier(neighbor_row, neighbor_column)))
            tile_ids, front_bounds, area = measured[row, column]
            export_bounds = front_view_bounds(model, front_bounds)
            panels.append(PanelPlan(
                panel_id, row, column, tile_ids, export_bounds, area,
                tuple(neighbors),
            ))
    mean_area = sum(panel.area_mm2 for panel in panels) / len(panels)
    balance = max(abs(panel.area_mm2 - mean_area) / mean_area for panel in panels)
    turns, vertices, seam_length = _seam_complexity(ownership, edge_owners)
    regularity = sum(abs(value - target) for value, target in zip(x_cuts, targets_x)) + sum(
        abs(value - target) for value, target in zip(y_cuts, targets_y)
    )
    score: tuple[float | int, ...] = (
        rows * columns, round(balance, 12), turns, vertices,
        seam_length, round(regularity, 9), rows, columns,
    )
    return tuple(panels), ownership, score


def panelize_model(
    model: ResolvedFabricationModel,
    *,
    mode: FabricationMode | str | None = None,
    safe_envelope_mm: tuple[float, float] | None = None,
    maximum_extra_panels: int | None = None,
) -> PanelizationPlan:
    mode_definition = resolve_fabrication_mode(mode)
    safe_envelope_mm = (
        mode_definition.safe_envelope_mm
        if safe_envelope_mm is None else safe_envelope_mm
    )
    bounds = fabrication_perimeter_bounds(model)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    theoretical_rows, theoretical_columns = theoretical_grid_counts(
        width, height, safe_envelope_mm,
    )
    cells, adjacency, edge_owners = _lattice_topology(model)
    center_x = tuple(sorted({round(tile.center_mm[0], 9) for tile in model.tiles}))
    center_y = tuple(sorted({round(tile.center_mm[1], 9) for tile in model.tiles}))
    minimum_count = theoretical_rows * theoretical_columns
    maximum_extra = maximum_extra_panels if maximum_extra_panels is not None else max(
        8, 2 * (theoretical_rows + theoretical_columns),
    )
    attempted = []
    for panel_count in range(minimum_count, minimum_count + maximum_extra + 1):
        valid = []
        layouts = sorted(
            (rows, columns)
            for rows in range(theoretical_rows, panel_count + 1)
            for columns in range(theoretical_columns, panel_count + 1)
            if rows * columns == panel_count
        )
        for rows, columns in layouts:
            attempted.append((rows, columns))
            targets_x = tuple(bounds[0] + width * index / columns for index in range(1, columns))
            targets_y = tuple(bounds[1] + height * index / rows for index in range(1, rows))
            x_sets = tuple(
                cuts for cuts in _candidate_cut_sets(center_x, columns, targets_x)
                if _axis_bounds_fit(model, cells, cuts, 0, safe_envelope_mm[0])
            )[:48]
            y_sets = tuple(
                cuts for cuts in _candidate_cut_sets(center_y, rows, targets_y)
                if _axis_bounds_fit(model, cells, cuts, 1, safe_envelope_mm[1])
            )[:48]
            for x_cuts, y_cuts in product(x_sets, y_sets):
                result = _evaluate_candidate(
                    model, rows, columns, x_cuts, y_cuts, cells, adjacency,
                    edge_owners, safe_envelope_mm, targets_x, targets_y,
                )
                if result is not None:
                    valid.append((result[2], x_cuts, y_cuts, result[0], result[1]))
        if valid:
            score, x_cuts, y_cuts, panels, ownership = min(
                valid, key=lambda value: (value[0], value[1], value[2]),
            )
            return PanelizationPlan(
                model, mode_definition.mode, safe_envelope_mm,
                theoretical_rows, theoretical_columns,
                panels[-1].row + 1, max(panel.column for panel in panels) + 1,
                x_cuts, y_cuts, panels, tuple(sorted(ownership.items())), score,
                tuple(attempted),
            )
    largest_tile = max(
        (
            max(point[0] for point in cells[tile.tile_id]) - min(point[0] for point in cells[tile.tile_id]),
            max(point[1] for point in cells[tile.tile_id]) - min(point[1] for point in cells[tile.tile_id]),
            tile.tile_id,
        )
        for tile in model.tiles
    )
    raise PanelizationError(
        "No valid natural-grout panelization fits the "
        f"{safe_envelope_mm[0]:g} x {safe_envelope_mm[1]:g} mm safe envelope. "
        f"Attempted layouts: {attempted}. Largest parent cell: "
        f"{largest_tile[0]:.3f} x {largest_tile[1]:.3f} mm ({largest_tile[2]}). "
        "No tiles were cut."
    )


def _point_in_polygon(point: Point2MM, polygon: tuple[Point2MM, ...]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        if (first[1] > y) == (second[1] > y):
            continue
        crossing = first[0] + (y - first[1]) * (second[0] - first[0]) / (second[1] - first[1])
        if x < crossing:
            inside = not inside
    return inside


def _point_segment_distance(point: Point2MM, first: Point2MM, second: Point2MM) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    length_squared = dx * dx + dy * dy
    scale = max(0.0, min(1.0, (
        (point[0] - first[0]) * dx + (point[1] - first[1]) * dy
    ) / length_squared))
    return sqrt(
        (point[0] - first[0] - scale * dx) ** 2
        + (point[1] - first[1] - scale * dy) ** 2
    )


def _marking_for_panel(
    panel: PanelPlan,
    cells: dict[str, tuple[Point2MM, ...]],
    tiles: dict[str, ResolvedTile],
) -> tuple[tuple[Point2MM, ...], ...]:
    identity = PanelIdentity(panel.panel_id, panel.row, panel.column)
    width, height = _marking_dimensions(panel.panel_id)
    boundary_counts: dict[tuple[Point2MM, Point2MM], int] = {}
    for tile_id in panel.tile_ids:
        polygon = cells[tile_id]
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            edge = _edge_key(first, second)
            boundary_counts[edge] = boundary_counts.get(edge, 0) + 1
    boundary = tuple(edge for edge, count in boundary_counts.items() if count == 1)
    candidates = sorted(
        (tiles[tile_id] for tile_id in panel.tile_ids),
        key=lambda tile: (-min(
            _point_segment_distance(tile.center_mm, first, second)
            for first, second in boundary
        ), tile.center_mm[1], tile.center_mm[0], tile.tile_id),
    )
    panel_polygons = tuple(cells[tile_id] for tile_id in panel.tile_ids)
    for tile in candidates:
        origin_x = tile.center_mm[0] - width / 2.0
        origin_y = tile.center_mm[1] - height / 2.0
        marking = _marking_cells_at(identity, origin_x, origin_y)
        points = [point for cell in marking for point in cell]
        if not all(any(_point_in_polygon(point, polygon) for polygon in panel_polygons) for point in points):
            continue
        if min(
            _point_segment_distance(point, first, second)
            for point in points for first, second in boundary
        ) < 1.0:
            continue
        return marking
    raise PanelizationError(
        f"Panel {panel.panel_id} has no seam-clear interior location for its backside ID."
    )


def build_panelized_fabrication(plan: PanelizationPlan) -> PanelizedFabrication:
    model = plan.model
    ownership = dict(plan.tile_ownership)
    tile_by_id = {tile.tile_id: tile for tile in model.tiles}
    cells = {tile.tile_id: _expanded_parent_cell(model, tile) for tile in model.tiles}
    fabrication_bounds = fabrication_perimeter_bounds(model)
    geometries = []
    markings = []
    for panel in plan.panels:
        panel_tiles = tuple(tile_by_id[tile_id] for tile_id in panel.tile_ids)
        marking = _marking_for_panel(panel, cells, tile_by_id)
        # Rear IDs are composed and reflected locally first. The independent
        # whole-panel front-to-export conversion below reflects them once more,
        # leaving the physical rear readable without touching artwork meaning.
        markings.append((panel.panel_id, marking))
        base = MeshBody(
            f"panel-{panel.panel_id.lower()}-base", f"Panel {panel.panel_id} Base", "base",
            debossed_cell_union_base_mesh(
                tuple(cells[tile.tile_id] for tile in panel_tiles),
                fabrication_bounds, marking,
                top_z_mm=model.profile.base_thickness_mm,
                deboss_depth_mm=PANEL_ID_DEBOSS_DEPTH_MM,
            ),
        )
        grout = MeshBody(
            f"panel-{panel.panel_id.lower()}-grout-thinset",
            f"Panel {panel.panel_id} Grout/Thinset", "grout-thinset",
            concave_grout_mesh(model, tiles=panel_tiles),
        )
        bodies = [base, grout]
        for channel in (value for value in model.channels if value.kind == "tile_color"):
            channel_tiles = tuple(
                tile for tile in panel_tiles if tile.material_channel_id == channel.channel_id
            )
            if channel_tiles:
                bodies.append(_panel_tile_body(model, panel.panel_id, channel, channel_tiles))
        export_bodies = tuple(
            _export_body_x(body, model.artwork_width_mm) for body in bodies
        )
        geometries.append(SinglePanelGeometry(
            panel.panel_id, model, export_bodies, panel.bounds_mm,
        ))
    assert set(ownership) == set(tile_by_id)
    return PanelizedFabrication(plan, tuple(geometries), tuple(markings))


def _shared_seams(plan: PanelizationPlan) -> list[dict[str, object]]:
    ownership = dict(plan.tile_ownership)
    _cells, _adjacency, edge_owners = _lattice_topology(plan.model)
    grouped: dict[tuple[str, str], list[tuple[Point2MM, Point2MM]]] = {}
    for edge, tile_ids in edge_owners.items():
        if len(tile_ids) != 2:
            continue
        first, second = ownership[tile_ids[0]], ownership[tile_ids[1]]
        if first == second:
            continue
        grouped.setdefault(tuple(sorted((first, second))), []).append(edge)
    return [{
        "panels": list(pair),
        "kind": "natural_parent_cell_grout_centerline",
        "edges_mm": [[list(first), list(second)] for first, second in sorted(edges)],
    } for pair, edges in sorted(grouped.items())]


def generate_panelization_package(
    model: ResolvedFabricationModel,
    output_directory: str | Path,
    *,
    mode: FabricationMode | str | None = None,
) -> PanelizationPackage:
    mode_definition = resolve_fabrication_mode(mode)
    plan = panelize_model(model, mode=mode_definition.mode)
    fabrication = build_panelized_fabrication(plan)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    ownership = dict(plan.tile_ownership)
    marking_by_panel = dict(fabrication.marking_cells)
    stl_paths, body_records = [], []
    for geometry in fabrication.panels:
        panel_directory = output / geometry.panel_id
        panel_directory.mkdir(parents=True, exist_ok=True)
        for body in geometry.bodies:
            filename = body.name.replace("/", "-").replace(" ", "_") + ".stl"
            path = write_mesh_stl(body, panel_directory / filename)
            parsed = MeshBody(body.body_id, body.name, body.material_channel_id, parse_ascii_stl(path))
            source_validation, round_trip = mesh_validation(body), mesh_validation(parsed)
            if not source_validation["watertight"] or not round_trip["watertight"]:
                raise ValueError(f"{body.name} failed panelized STL validation.")
            stl_paths.append(path)
            body_records.append({
                "panel_id": geometry.panel_id,
                "channel": body.material_channel_id,
                "filename": f"{geometry.panel_id}/{filename}",
                "bounds_mm": list(body.bounds_mm),
                "tile_ids": list(body.tile_ids),
                "mesh_validation": source_validation,
                "stl_round_trip_valid": len(parsed.triangles) == len(body.triangles) and round_trip["watertight"],
                "geometry_signature_sha256": sha256(json.dumps(body.triangles, separators=(",", ":")).encode()).hexdigest(),
                "stl_sha256": sha256(path.read_bytes()).hexdigest(),
            })
    panel_records = []
    for panel in plan.panels:
        panel_records.append({
            "panel_id": panel.panel_id, "row": panel.row, "column": panel.column,
            "bounds_mm": list(panel.bounds_mm), "width_mm": panel.width_mm,
            "height_mm": panel.height_mm, "area_mm2": panel.area_mm2,
            "front_view_bounds_mm": list(front_view_bounds(model, panel.bounds_mm)),
            "tile_count": len(panel.tile_ids), "tile_ids": list(panel.tile_ids),
            "neighbors": dict(panel.neighbors),
            "print_rotation_degrees": panel.print_rotation_degrees,
            "fits_safe_envelope": panel.fits_safe_envelope,
            "backside_marking": {
                "content": panel.panel_id, "cell_size_mm": PANEL_ID_CELL_MM,
                "depth_mm": PANEL_ID_DEBOSS_DEPTH_MM, "mirrored": True,
                "reading_direction": "left_to_right_when_viewed_from_backside",
                "coordinate_scope": "glyph_only",
                "cell_count": len(marking_by_panel[panel.panel_id]),
            },
        })
    signature_payload = {
        "plan": [(panel.panel_id, panel.tile_ids, panel.bounds_mm) for panel in plan.panels],
        "bodies": [(record["filename"], record["geometry_signature_sha256"]) for record in body_records],
    }
    signature = sha256(json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    bounds = fabrication_perimeter_bounds(model)
    manifest = {
        "schema": {"name": PANELIZATION_SCHEMA, "version": PANELIZATION_SCHEMA_VERSION},
        "application_version": __version__,
        "fabrication_mode": {
            "id": mode_definition.mode_id,
            "display_name": mode_definition.display_name,
        },
        "printer_profile_id": "bambu-p1s-mosaica-mode-envelope-v1",
        "safe_panel_envelope_mm": {
            "width": plan.safe_envelope_mm[0], "height": plan.safe_envelope_mm[1],
        },
        "source_fabricated_dimensions_mm": {"width": bounds[2] - bounds[0], "height": bounds[3] - bounds[1]},
        "theoretical_starting_grid": {"rows": plan.theoretical_rows, "columns": plan.theoretical_columns},
        "final_grid": {"rows": plan.rows, "columns": plan.columns, "panel_count": len(plan.panels)},
        "optimization": {
            "priority": ["panel_count", "maximum_normalized_area_deviation", "seam_turns", "seam_vertices", "seam_length_mm", "ideal_cut_deviation_mm", "grid_order"],
            "score": list(plan.score), "attempted_layouts": [list(value) for value in plan.attempted_layouts],
        },
        "physical_profile": model.profile.__dict__,
        "panels": panel_records,
        "shared_seams": _shared_seams(plan),
        "body_channel_ownership": body_records,
        "tile_assignment": {"all_tiles_assigned_exactly_once": len(ownership) == len(model.tiles), "tile_cuts_created": 0},
        "panel_connection": {"type": "natural_grout_line_seam", "dedicated_connector_geometry": False, "permanent_structure": "ACP/backer_and_adhesive"},
        "coordinate_system": {"units": "mm", "internal": "export_geometry_coordinates", "finished_front": "x_reflected_from_export_geometry", "stl": "shared_global_coordinates_per_panel_multipart_object"},
        "geometry_signature_sha256": signature,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return PanelizationPackage(output, manifest_path, tuple(stl_paths), signature)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Panelize a saved Mosaica project along natural grout lines.")
    parser.add_argument("--project", required=True, help="saved MosaicProject JSON")
    parser.add_argument("--out", default="fabricate_panelized_review")
    parser.add_argument(
        "--mode", choices=tuple(value.value for value in FabricationMode), default="studio",
    )
    arguments = parser.parse_args(argv)
    project = MosaicProject.load(arguments.project)
    model = resolve_mosaic_project(project, PRODUCTION_PROFILE)
    package = generate_panelization_package(model, arguments.out, mode=arguments.mode)
    print(f"Fabricate panelized review: {package.output_directory}")
    print(f"Manifest: {package.manifest_path}")
    print(f"Geometry signature: {package.geometry_signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

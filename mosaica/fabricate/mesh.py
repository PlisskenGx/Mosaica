from __future__ import annotations

from dataclasses import dataclass
from math import atan2, ceil, cos, degrees, pi, sin, sqrt

from .model import Point2MM, ResolvedFabricationModel, ResolvedTile


Point3MM = tuple[float, float, float]
Triangle3 = tuple[Point3MM, Point3MM, Point3MM]


@dataclass(frozen=True)
class MeshBody:
    body_id: str
    name: str
    material_channel_id: str
    triangles: tuple[Triangle3, ...]
    tile_ids: tuple[str, ...] = ()
    solid_triangle_counts: tuple[int, ...] = ()

    @property
    def bounds_mm(self) -> tuple[float, float, float, float, float, float]:
        points = [point for triangle in self.triangles for point in triangle]
        return (
            min(value[0] for value in points),
            min(value[1] for value in points),
            min(value[2] for value in points),
            max(value[0] for value in points),
            max(value[1] for value in points),
            max(value[2] for value in points),
        )


@dataclass(frozen=True)
class SinglePanelGeometry:
    panel_id: str
    model: ResolvedFabricationModel
    bodies: tuple[MeshBody, ...]
    fabrication_bounds_mm: tuple[float, float, float, float]

    def body(self, channel_id: str) -> MeshBody:
        try:
            return next(value for value in self.bodies if value.material_channel_id == channel_id)
        except StopIteration as exc:
            raise ValueError(f"Panel has no body for channel: {channel_id}") from exc


def _area(points: tuple[Point2MM, ...]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )


def _ccw(points: tuple[Point2MM, ...]) -> tuple[Point2MM, ...]:
    if len(points) < 3:
        raise ValueError("A fabrication polygon must have at least three vertices.")
    values_list: list[Point2MM] = []
    for point in points:
        if not values_list or (
            abs(point[0] - values_list[-1][0]) > 1e-8
            or abs(point[1] - values_list[-1][1]) > 1e-8
        ):
            values_list.append(point)
    if len(values_list) > 1 and (
        abs(values_list[0][0] - values_list[-1][0]) <= 1e-8
        and abs(values_list[0][1] - values_list[-1][1]) <= 1e-8
    ):
        values_list.pop()
    values = tuple(values_list)
    if len(values) < 3:
        raise ValueError("A fabrication polygon must enclose physical area.")
    values = values if _area(values) > 0 else tuple(reversed(values))
    # Panel clipping can retain original vertices that become collinear with a
    # rectangular cut. Removing them avoids zero-area fan triangles and gives
    # every crown ring one strict convex vertex per physical corner.
    while len(values) > 3:
        remove_index = None
        for index, current in enumerate(values):
            previous = values[index - 1]
            following = values[(index + 1) % len(values)]
            cross = (
                (current[0] - previous[0]) * (following[1] - current[1])
                - (current[1] - previous[1]) * (following[0] - current[0])
            )
            if abs(cross) <= 1e-9:
                remove_index = index
                break
        if remove_index is None:
            break
        values = values[:remove_index] + values[remove_index + 1:]
    return values


def _cap(points: tuple[Point2MM, ...], z: float, upward: bool) -> list[Triangle3]:
    ring = tuple((x, y, z) for x, y in points)
    result = []
    for index in range(1, len(ring) - 1):
        result.append(
            (ring[0], ring[index], ring[index + 1])
            if upward else (ring[0], ring[index + 1], ring[index])
        )
    return result


def _join_rings(lower: tuple[Point3MM, ...], upper: tuple[Point3MM, ...]) -> list[Triangle3]:
    if len(lower) != len(upper):
        raise ValueError("Fabrication mesh rings must have matching vertices.")
    result = []
    for index in range(len(lower)):
        following = (index + 1) % len(lower)
        result.extend((
            (lower[index], lower[following], upper[following]),
            (lower[index], upper[following], upper[index]),
        ))
    return result


def _line_intersection(
    first_start: Point2MM,
    first_end: Point2MM,
    second_start: Point2MM,
    second_end: Point2MM,
) -> Point2MM:
    ax = first_end[0] - first_start[0]
    ay = first_end[1] - first_start[1]
    bx = second_end[0] - second_start[0]
    by = second_end[1] - second_start[1]
    determinant = ax * by - ay * bx
    if abs(determinant) <= 1e-12:
        raise ValueError("Rounded crown offset produced parallel polygon edges.")
    cx = second_start[0] - first_start[0]
    cy = second_start[1] - first_start[1]
    scale = (cx * by - cy * bx) / determinant
    return first_start[0] + scale * ax, first_start[1] + scale * ay


def inset_polygon_preserving_origin(
    points: tuple[Point2MM, ...], inset_mm: float,
) -> tuple[Point2MM, ...]:
    """Inset a convex polygon without translating its fabrication origin."""

    if inset_mm < 0:
        raise ValueError("Crown inset cannot be negative.")
    points = _ccw(points)
    shifted = []
    for first, second in zip(points, points[1:] + points[:1]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = sqrt(dx * dx + dy * dy)
        if length <= 1e-10:
            raise ValueError("Rounded crown polygon contains a zero-length edge.")
        # A CCW polygon's left normal points inward.
        nx, ny = -dy / length, dx / length
        shifted.append((
            (first[0] + nx * inset_mm, first[1] + ny * inset_mm),
            (second[0] + nx * inset_mm, second[1] + ny * inset_mm),
        ))
    result = tuple(
        _line_intersection(
            shifted[index - 1][0], shifted[index - 1][1],
            shifted[index][0], shifted[index][1],
        )
        for index in range(len(points))
    )
    if len(result) != len(points) or _area(result) <= 1e-8:
        raise ValueError("Rounded crown inset collapses the tile polygon.")
    return result


def extruded_polygon_mesh(
    points: tuple[Point2MM, ...], bottom_z_mm: float, top_z_mm: float,
) -> tuple[Triangle3, ...]:
    if top_z_mm <= bottom_z_mm:
        raise ValueError("Fabrication extrusion height must be positive.")
    points = _ccw(points)
    bottom = tuple((x, y, bottom_z_mm) for x, y in points)
    top = tuple((x, y, top_z_mm) for x, y in points)
    return tuple(
        _cap(points, bottom_z_mm, False)
        + _join_rings(bottom, top)
        + _cap(points, top_z_mm, True)
    )


def _point_in_polygon(point: Point2MM, polygon: tuple[Point2MM, ...]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        if (first[1] > y) == (second[1] > y):
            continue
        crossing = first[0] + (
            (y - first[1]) * (second[0] - first[0])
            / (second[1] - first[1])
        )
        if x < crossing:
            inside = not inside
    return inside


def _point_segment_distance(
    point: Point2MM, first: Point2MM, second: Point2MM,
) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return sqrt((point[0] - first[0]) ** 2 + (point[1] - first[1]) ** 2)
    scale = max(0.0, min(1.0, (
        (point[0] - first[0]) * dx + (point[1] - first[1]) * dy
    ) / length_squared))
    nearest = first[0] + scale * dx, first[1] + scale * dy
    return sqrt((point[0] - nearest[0]) ** 2 + (point[1] - nearest[1]) ** 2)


def grout_surface_z(
    point: Point2MM,
    tile_polygons: tuple[tuple[Point2MM, ...], ...],
    grout_top_z_mm: float,
    grout_gap_mm: float,
    depression_mm: float,
) -> float:
    """Return a smooth shallow grout heightfield value."""

    if depression_mm <= 0:
        return grout_top_z_mm
    for polygon in tile_polygons:
        if _point_in_polygon(point, polygon):
            return grout_top_z_mm
    distance = min(
        _point_segment_distance(point, first, second)
        for polygon in tile_polygons
        for first, second in zip(polygon, polygon[1:] + polygon[:1])
    )
    phase = min(1.0, distance / (grout_gap_mm / 2.0))
    depth = depression_mm * sin((pi / 2.0) * phase) ** 2
    return round(grout_top_z_mm - depth, 9)


def x_monotone_heightfield_mesh(
    y_values_mm: tuple[float, ...],
    left_x_at_y,
    right_x_at_y,
    bottom_z_mm: float,
    top_z_at_xy,
    mesh_step_mm: float,
) -> tuple[Triangle3, ...]:
    """Mesh a watertight X-monotone solid with a deterministic heightfield top."""

    y_values = tuple(sorted(set(round(value, 9) for value in y_values_mm)))
    if len(y_values) < 2:
        raise ValueError("A heightfield requires at least two Y rows.")
    maximum_width = max(right_x_at_y(y) - left_x_at_y(y) for y in y_values)
    if maximum_width <= 0:
        raise ValueError("A heightfield must have positive width.")
    columns = max(1, ceil(maximum_width / mesh_step_mm))
    top_rows: list[tuple[Point3MM, ...]] = []
    bottom_rows: list[tuple[Point3MM, ...]] = []
    for y in y_values:
        left, right = left_x_at_y(y), right_x_at_y(y)
        if right <= left:
            raise ValueError("Heightfield boundaries cross or collapse.")
        top_row = []
        bottom_row = []
        for column in range(columns + 1):
            x = round(left + (right - left) * column / columns, 9)
            top_row.append((x, y, top_z_at_xy(x, y)))
            bottom_row.append((x, y, bottom_z_mm))
        top_rows.append(tuple(top_row))
        bottom_rows.append(tuple(bottom_row))

    triangles: list[Triangle3] = []
    for row in range(len(y_values) - 1):
        for column in range(columns):
            tl, tr = top_rows[row][column], top_rows[row][column + 1]
            bl, br = top_rows[row + 1][column], top_rows[row + 1][column + 1]
            triangles.extend(((tl, tr, br), (tl, br, bl)))
            tl, tr = bottom_rows[row][column], bottom_rows[row][column + 1]
            bl, br = bottom_rows[row + 1][column], bottom_rows[row + 1][column + 1]
            triangles.extend(((tl, br, tr), (tl, bl, br)))

    boundary_top = (
        list(top_rows[0])
        + [row[-1] for row in top_rows[1:]]
        + list(reversed(top_rows[-1][:-1]))
        + [row[0] for row in reversed(top_rows[1:-1])]
    )
    boundary_bottom = tuple((x, y, bottom_z_mm) for x, y, _ in boundary_top)
    triangles.extend(_join_rings(boundary_bottom, tuple(boundary_top)))
    return tuple(triangles)


def debossed_x_monotone_base_mesh(
    outline: tuple[Point2MM, ...],
    left_x_at_y,
    right_x_at_y,
    holes: tuple[tuple[Point2MM, ...], ...],
    *,
    top_z_mm: float,
    deboss_depth_mm: float,
) -> tuple[Triangle3, ...]:
    """Create an X-monotone Base with deterministic backside cavities.

    Each cavity is an axis-aligned rectangle.  Its edges are inserted into the
    planar subdivision, so the exported mesh contains no Boolean or font
    dependency and no unconstrained approximation of the marking geometry.
    """

    outline = _ccw(outline)
    if not 0.0 < deboss_depth_mm < top_z_mm:
        raise ValueError("Backside deboss depth must remain inside the Base.")
    for hole in holes:
        if len(hole) != 4:
            raise ValueError("Backside marking cells must be rectangles.")

    xs = {round(point[0], 9) for point in outline}
    ys = {round(point[1], 9) for point in outline}
    for hole in holes:
        xs.update(round(point[0], 9) for point in hole)
        ys.update(round(point[1], 9) for point in hole)
    # Split every band where a sloping panel edge crosses a marking X line;
    # this prevents T-junctions where the constrained cell grid meets a seam.
    for first, second in zip(outline, outline[1:] + outline[:1]):
        if abs(second[0] - first[0]) <= 1e-12:
            continue
        for x in tuple(xs):
            scale = (x - first[0]) / (second[0] - first[0])
            if 1e-9 < scale < 1.0 - 1e-9:
                ys.add(round(first[1] + scale * (second[1] - first[1]), 9))
    x_values, y_values = tuple(sorted(xs)), tuple(sorted(ys))

    def clip_halfplane(
        polygon: tuple[Point2MM, ...], signed_distance,
    ) -> tuple[Point2MM, ...]:
        result = []
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            first_distance = signed_distance(first)
            second_distance = signed_distance(second)
            first_inside = first_distance >= -1e-9
            second_inside = second_distance >= -1e-9
            if first_inside:
                result.append(first)
            if first_inside != second_inside:
                scale = first_distance / (first_distance - second_distance)
                result.append((
                    round(first[0] + scale * (second[0] - first[0]), 9),
                    round(first[1] + scale * (second[1] - first[1]), 9),
                ))
        return tuple(result)

    def in_hole(x: float, y: float) -> bool:
        return any(
            min(point[0] for point in hole) < x < max(point[0] for point in hole)
            and min(point[1] for point in hole) < y < max(point[1] for point in hole)
            for hole in holes
        )

    top_triangles: list[Triangle3] = []
    bottom_triangles: list[Triangle3] = []
    for y0, y1 in zip(y_values, y_values[1:]):
        if y1 - y0 <= 1e-9:
            continue
        left0, left1 = left_x_at_y(y0), left_x_at_y(y1)
        right0, right1 = right_x_at_y(y0), right_x_at_y(y1)

        def left_at(y: float) -> float:
            return left0 + (left1 - left0) * (y - y0) / (y1 - y0)

        def right_at(y: float) -> float:
            return right0 + (right1 - right0) * (y - y0) / (y1 - y0)

        for x0, x1 in zip(x_values, x_values[1:]):
            cell = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            cell = clip_halfplane(cell, lambda point: point[0] - left_at(point[1]))
            if len(cell) < 3:
                continue
            cell = clip_halfplane(cell, lambda point: right_at(point[1]) - point[0])
            if len(cell) < 3 or abs(_area(cell)) <= 1e-10:
                continue
            cell = _ccw(cell)
            top_triangles.extend(_cap(cell, top_z_mm, True))
            midpoint = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if not in_hole(*midpoint):
                bottom_triangles.extend(_cap(cell, 0.0, False))

    boundary_top = _surface_boundary_loop(tuple(top_triangles))
    boundary_bottom = tuple((x, y, 0.0) for x, y, _ in boundary_top)
    triangles = top_triangles + bottom_triangles
    triangles.extend(_join_rings(boundary_bottom, boundary_top))
    bottom_loops = _surface_boundary_loops(tuple(bottom_triangles))
    outer_loop = max(
        bottom_loops,
        key=lambda loop: abs(_area(tuple((x, y) for x, y, _ in loop))),
    )
    for loop in bottom_loops:
        if loop == outer_loop:
            continue
        if _area(tuple((x, y) for x, y, _ in loop)) > 0:
            loop = tuple(reversed(loop))
        cavity_bottom = tuple((x, y, 0.0) for x, y, _ in loop)
        cavity_top = tuple((x, y, deboss_depth_mm) for x, y, _ in loop)
        triangles.extend(_join_rings(cavity_bottom, cavity_top))
        center = (
            round(sum(x for x, _y, _z in cavity_top) / len(cavity_top), 9),
            round(sum(y for _x, y, _z in cavity_top) / len(cavity_top), 9),
            deboss_depth_mm,
        )
        triangles.extend(
            (center, cavity_top[index], cavity_top[(index + 1) % len(cavity_top)])
            for index in range(len(cavity_top))
        )
    return tuple(triangles)


def _clip_surface_to_axis(
    polygon: tuple[Point3MM, ...],
    axis: int,
    plane_mm: float,
    keep_greater: bool,
) -> tuple[Point3MM, ...]:
    tolerance = 1e-9

    def inside(point: Point3MM) -> bool:
        return (
            point[axis] >= plane_mm - tolerance
            if keep_greater else point[axis] <= plane_mm + tolerance
        )

    def intersection(first: Point3MM, second: Point3MM) -> Point3MM:
        scale = (plane_mm - first[axis]) / (second[axis] - first[axis])
        return tuple(
            plane_mm if index == axis else round(
                first[index] + scale * (second[index] - first[index]), 9,
            )
            for index in range(3)
        )  # type: ignore[return-value]

    result = []
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        first_inside, second_inside = inside(first), inside(second)
        if first_inside:
            result.append(first)
        if first_inside != second_inside:
            result.append(intersection(first, second))
    deduplicated = []
    for point in result:
        if not deduplicated or point != deduplicated[-1]:
            deduplicated.append(point)
    if len(deduplicated) > 1 and deduplicated[0] == deduplicated[-1]:
        deduplicated.pop()
    return tuple(deduplicated)


def _clip_surface_triangles(
    triangles: tuple[Triangle3, ...],
    bounds: tuple[float, float, float, float],
) -> tuple[Triangle3, ...]:
    result = []
    left, top, right, bottom = bounds
    for triangle in triangles:
        polygon: tuple[Point3MM, ...] = tuple((
            left if abs(point[0] - left) <= 1e-6 else (
                right if abs(point[0] - right) <= 1e-6 else point[0]
            ),
            top if abs(point[1] - top) <= 1e-6 else (
                bottom if abs(point[1] - bottom) <= 1e-6 else point[1]
            ),
            point[2],
        ) for point in triangle)
        for axis, plane, keep_greater in (
            (0, left, True), (0, right, False),
            (1, top, True), (1, bottom, False),
        ):
            polygon = _clip_surface_to_axis(
                polygon, axis, plane, keep_greater,
            )
            if len(polygon) < 3:
                break
        for index in range(1, len(polygon) - 1):
            candidate = (polygon[0], polygon[index], polygon[index + 1])
            if sum(value * value for value in _triangle_normal_raw(candidate)) > 1e-18:
                result.append(candidate)
    def canonical(point: Point3MM) -> Point3MM:
        return (
            left if abs(point[0] - left) <= 1e-8 else (
                right if abs(point[0] - right) <= 1e-8 else round(point[0], 9)
            ),
            top if abs(point[1] - top) <= 1e-8 else (
                bottom if abs(point[1] - bottom) <= 1e-8 else round(point[1], 9)
            ),
            round(point[2], 9),
        )

    canonical_triangles = tuple(
        tuple(canonical(point) for point in triangle)  # type: ignore[arg-type]
        for triangle in result
    )
    return tuple(
        triangle for triangle in canonical_triangles
        if sum(value * value for value in _triangle_normal_raw(triangle)) > 1e-18
    )


def _orient_surface_up(triangle: Triangle3) -> Triangle3:
    return (
        triangle if _triangle_normal_raw(triangle)[2] >= 0
        else (triangle[0], triangle[2], triangle[1])
    )


def _tile_cell_surface(
    model: ResolvedFabricationModel,
    tile: ResolvedTile,
) -> tuple[Triangle3, ...]:
    """Create exact edge-driven grout rings for one parent hex cell."""

    profile = model.profile
    grout_top = profile.base_thickness_mm + profile.grout_thickness_mm
    inner = _ccw(tile.full_polygon_mm)
    center_x, center_y = tile.center_mm
    ratio = (
        model.tile_flat_to_flat_mm + model.grout_gap_mm
    ) / model.tile_flat_to_flat_mm
    outer = tuple((
        round(center_x + (x - center_x) * ratio, 6),
        round(center_y + (y - center_y) * ratio, 6),
    ) for x, y in inner)
    transverse_segments = max(
        1, ceil((model.grout_gap_mm / 2.0) / profile.grout_mesh_step_mm),
    )
    rings = []
    for segment in range(transverse_segments + 1):
        phase = segment / transverse_segments
        outline = tuple(
            inner[index] if segment == 0 else (
                outer[index] if segment == transverse_segments else (
                    round(inner[index][0] + phase * (outer[index][0] - inner[index][0]), 9),
                    round(inner[index][1] + phase * (outer[index][1] - inner[index][1]), 9),
                )
            )
            for index in range(len(inner))
        )
        rings.append(tuple((
            x, y, grout_surface_z(
                (x, y), (inner,), grout_top, model.grout_gap_mm,
                profile.grout_depression_mm,
            )
        ) for x, y in outline))
    triangles = _cap(inner, grout_top, True)
    for first, second in zip(rings, rings[1:]):
        triangles.extend(
            _orient_surface_up(value) for value in _join_rings(first, second)
        )
    return tuple(triangles)


def _surface_boundary_loops(
    triangles: tuple[Triangle3, ...],
) -> tuple[tuple[Point3MM, ...], ...]:
    edge_counts: dict[tuple[Point3MM, Point3MM], int] = {}
    for triangle in triangles:
        for first, second in zip(triangle, triangle[1:] + triangle[:1]):
            edge = tuple(sorted((first, second)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    boundary_edges = [edge for edge, count in edge_counts.items() if count == 1]
    adjacency: dict[Point3MM, list[Point3MM]] = {}
    for first, second in boundary_edges:
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError("Constrained surface contains a non-manifold boundary.")
    unused = set(adjacency)
    loops = []
    while unused:
        start = min(unused, key=lambda point: (point[1], point[0], point[2]))
        loop = [start]
        previous = None
        current = start
        while True:
            candidates = sorted(value for value in adjacency[current] if value != previous)
            following = candidates[0]
            if following == start:
                break
            if following in loop:
                raise ValueError("Constrained surface boundary self-intersects.")
            loop.append(following)
            previous, current = current, following
        unused.difference_update(loop)
        loops.append(tuple(loop))
    return tuple(loops)


def _surface_boundary_loop(
    triangles: tuple[Triangle3, ...],
) -> tuple[Point3MM, ...]:
    loops = _surface_boundary_loops(triangles)
    if len(loops) != 1:
        raise ValueError("Constrained grout surface does not have one manifold boundary.")
    loop = list(loops[0])
    if _area(tuple((x, y) for x, y, _ in loop)) < 0:
        loop.reverse()
    return tuple(loop)


def concave_grout_mesh(
    model: ResolvedFabricationModel,
    *,
    tiles: tuple[ResolvedTile, ...] | None = None,
) -> tuple[Triangle3, ...]:
    """Build an exact tile-edge-constrained concave Grout/Thinset body."""

    profile = model.profile
    if profile.grout_surface != "concave":
        raise ValueError("Concave grout mesh requires a concave profile.")
    source_tiles = tiles if tiles is not None else model.tiles
    top_triangles = tuple(
        triangle
        for tile in source_tiles
        for triangle in _tile_cell_surface(model, tile)
    )
    top_triangles = _clip_surface_triangles(
        top_triangles, fabrication_perimeter_bounds(model),
    )
    boundary_top = _surface_boundary_loop(top_triangles)
    bottom_z = profile.base_thickness_mm
    bottom_triangles = tuple(
        (
            (triangle[0][0], triangle[0][1], bottom_z),
            (triangle[2][0], triangle[2][1], bottom_z),
            (triangle[1][0], triangle[1][1], bottom_z),
        )
        for triangle in top_triangles
    )
    boundary_bottom = tuple((x, y, bottom_z) for x, y, _ in boundary_top)
    return bottom_triangles + top_triangles + tuple(
        _join_rings(boundary_bottom, boundary_top)
    )


def concave_grout_spatial_validation(
    model: ResolvedFabricationModel,
    triangles: tuple[Triangle3, ...],
    *,
    tiles: tuple[ResolvedTile, ...] | None = None,
) -> dict[str, object]:
    """Measure edge fidelity and orientation parity on a concave grout mesh."""

    source_tiles = tiles if tiles is not None else model.tiles
    profile = model.profile
    grout_top = profile.base_thickness_mm + profile.grout_thickness_mm
    tolerance = 1e-7
    top_triangles = tuple(
        triangle for triangle in triangles
        if _triangle_normal_raw(triangle)[2] > tolerance
    )
    surface_edges = {
        tuple(sorted((first, second)))
        for triangle in top_triangles
        for first, second in zip(triangle, triangle[1:] + triangle[:1])
    }
    left, top, right, bottom = fabrication_perimeter_bounds(model)

    direction_counts = {"horizontal": 0, "+60": 0, "-60": 0}
    deviations = []
    boundary_z_values = {key: [] for key in direction_counts}
    for tile in source_tiles:
        polygon = _ccw(tile.full_polygon_mm)
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            if not all(
                left - tolerance <= point[0] <= right + tolerance
                and top - tolerance <= point[1] <= bottom + tolerance
                for point in (first, second)
            ):
                continue
            angle = degrees(atan2(second[1] - first[1], second[0] - first[0])) % 180.0
            direction = min(
                (("horizontal", 0.0), ("+60", 60.0), ("-60", 120.0)),
                key=lambda value: abs(angle - value[1]),
            )[0]
            start = (first[0], first[1], grout_top)
            end = (second[0], second[1], grout_top)
            edge = tuple(sorted((start, end)))
            if edge in surface_edges:
                deviation = 0.0
            else:
                candidates = [
                    max(
                        _point_segment_distance((value[0], value[1]), first, second)
                        for value in candidate
                    )
                    for candidate in surface_edges
                    if all(abs(value[2] - grout_top) <= tolerance for value in candidate)
                ]
                deviation = min(candidates, default=float("inf"))
            deviations.append(deviation)
            direction_counts[direction] += 1
            boundary_z_values[direction].extend((start[2], end[2]))

    depressed_vertices = {
        point for triangle in top_triangles for point in triangle
        if point[2] < grout_top - tolerance
    }
    protrusions = 0
    for x, y, _z in depressed_vertices:
        for tile in source_tiles:
            polygon = _ccw(tile.full_polygon_mm)
            edge_distance = min(
                _point_segment_distance((x, y), first, second)
                for first, second in zip(polygon, polygon[1:] + polygon[:1])
            )
            if edge_distance > tolerance and _point_in_polygon((x, y), polygon):
                protrusions += 1
                break

    junctions: dict[Point2MM, list[float]] = {}
    for tile in source_tiles:
        center_x, center_y = tile.center_mm
        ratio = (
            model.tile_flat_to_flat_mm + model.grout_gap_mm
        ) / model.tile_flat_to_flat_mm
        for x, y in _ccw(tile.full_polygon_mm):
            point = (
                round(center_x + (x - center_x) * ratio, 6),
                round(center_y + (y - center_y) * ratio, 6),
            )
            junctions.setdefault(point, []).append(
                grout_surface_z(
                    point, (_ccw(tile.full_polygon_mm),), grout_top,
                    model.grout_gap_mm, profile.grout_depression_mm,
                )
            )
    triple_junctions = [values for values in junctions.values() if len(values) >= 3]
    return {
        "boundary_edge_count_by_orientation": direction_counts,
        "boundary_z_range_by_orientation": {
            key: (min(values), max(values)) if values else None
            for key, values in boundary_z_values.items()
        },
        "maximum_boundary_deviation_mm": round(max(deviations, default=0.0), 12),
        "maximum_depression_mm": round(
            grout_top - min(point[2] for triangle in top_triangles for point in triangle),
            9,
        ),
        "transverse_segments_per_half_gap": max(
            1, ceil((model.grout_gap_mm / 2.0) / profile.grout_mesh_step_mm),
        ),
        "depressed_vertices_inside_tiles": protrusions,
        "triple_junction_count": len(triple_junctions),
        "maximum_triple_junction_z_spread_mm": max(
            (max(values) - min(values) for values in triple_junctions),
            default=0.0,
        ),
    }


def rounded_tile_rings(
    points: tuple[Point2MM, ...],
    grout_top_z_mm: float,
    straight_relief_mm: float,
    crown_mm: float,
    crown_segments: int,
) -> tuple[tuple[Point3MM, ...], ...]:
    """Create inspectable straight-wall and quarter-round crown rings."""

    points = _ccw(points)
    straight_z = grout_top_z_mm + straight_relief_mm
    rings: list[tuple[Point3MM, ...]] = [
        tuple((x, y, grout_top_z_mm) for x, y in points),
        tuple((x, y, straight_z) for x, y in points),
    ]
    for step in range(1, crown_segments + 1):
        angle = (pi / 2.0) * step / crown_segments
        inset = crown_mm * (1.0 - cos(angle))
        outline = inset_polygon_preserving_origin(points, inset)
        z = round(straight_z + crown_mm * sin(angle), 9)
        rings.append(tuple((x, y, z) for x, y in outline))
    return tuple(rings)


def rounded_tile_mesh(
    points: tuple[Point2MM, ...],
    grout_top_z_mm: float,
    straight_relief_mm: float,
    crown_mm: float,
    crown_segments: int,
) -> tuple[Triangle3, ...]:
    """Create straight sides plus a deterministic quarter-round crown."""

    points = _ccw(points)
    rings = rounded_tile_rings(
        points, grout_top_z_mm, straight_relief_mm, crown_mm, crown_segments,
    )
    triangles = _cap(points, grout_top_z_mm, False)
    for lower, upper in zip(rings, rings[1:]):
        triangles.extend(_join_rings(lower, upper))
    top_points = tuple((x, y) for x, y, _ in rings[-1])
    triangles.extend(_cap(top_points, rings[-1][0][2], True))
    return tuple(triangles)


def maximum_triangle_edge(triangles: tuple[Triangle3, ...]) -> float:
    return max(
        sqrt(sum((first[index] - second[index]) ** 2 for index in range(3)))
        for triangle in triangles
        for first, second in zip(triangle, triangle[1:] + triangle[:1])
    )


def polygon_diameter(points: tuple[Point2MM, ...]) -> float:
    return max(
        sqrt((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2)
        for first in points for second in points
    )


def _triangle_normal_raw(triangle: Triangle3) -> Point3MM:
    first, second, third = triangle
    left = tuple(second[index] - first[index] for index in range(3))
    right = tuple(third[index] - first[index] for index in range(3))
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def clip_mesh_to_axis_plane(
    triangles: tuple[Triangle3, ...],
    *,
    axis: int,
    plane_mm: float,
    keep_greater: bool,
) -> tuple[Triangle3, ...]:
    """Clip a closed convex mesh and cap the new planar manufactured side."""

    tolerance = 1e-10

    def inside(point: Point3MM) -> bool:
        return (
            point[axis] >= plane_mm - tolerance
            if keep_greater else point[axis] <= plane_mm + tolerance
        )

    def snap_to_plane(point: Point3MM) -> Point3MM:
        if abs(point[axis] - plane_mm) > tolerance:
            return point
        return tuple(
            plane_mm if index == axis else point[index]
            for index in range(3)
        )  # type: ignore[return-value]

    def intersection(first: Point3MM, second: Point3MM) -> Point3MM:
        if abs(first[axis] - plane_mm) <= tolerance:
            return snap_to_plane(first)
        if abs(second[axis] - plane_mm) <= tolerance:
            return snap_to_plane(second)
        denominator = second[axis] - first[axis]
        if abs(denominator) <= tolerance:
            return first
        scale = (plane_mm - first[axis]) / denominator
        values = tuple(round(
            plane_mm if index == axis else first[index] + scale * (second[index] - first[index]),
            10,
        ) for index in range(3))
        return values  # type: ignore[return-value]

    clipped_triangles: list[Triangle3] = []
    cut_points: dict[tuple[float, float, float], Point3MM] = {}
    for triangle in triangles:
        polygon = list(triangle)
        result: list[Point3MM] = []
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            first_inside = inside(first)
            second_inside = inside(second)
            if first_inside:
                result.append(snap_to_plane(first))
            if first_inside != second_inside:
                point = intersection(first, second)
                result.append(point)
                cut_points[point] = point
        deduplicated: list[Point3MM] = []
        for point in result:
            if not deduplicated or any(
                abs(point[index] - deduplicated[-1][index]) > tolerance
                for index in range(3)
            ):
                deduplicated.append(point)
        if len(deduplicated) > 1 and all(
            abs(deduplicated[0][index] - deduplicated[-1][index]) <= tolerance
            for index in range(3)
        ):
            deduplicated.pop()
        result = deduplicated
        if len(result) >= 3:
            for index in range(1, len(result) - 1):
                clipped_triangles.append((result[0], result[index], result[index + 1]))

    if len(cut_points) >= 3:
        other_axes = [value for value in range(3) if value != axis]
        all_points = list(cut_points.values())

        def cross(origin: Point3MM, first: Point3MM, second: Point3MM) -> float:
            return (
                (first[other_axes[0]] - origin[other_axes[0]])
                * (second[other_axes[1]] - origin[other_axes[1]])
                - (first[other_axes[1]] - origin[other_axes[1]])
                * (second[other_axes[0]] - origin[other_axes[0]])
            )

        coordinate_order = sorted(
            all_points,
            key=lambda point: (point[other_axes[0]], point[other_axes[1]]),
        )
        lower: list[Point3MM] = []
        for point in coordinate_order:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= tolerance:
                lower.pop()
            lower.append(point)
        upper: list[Point3MM] = []
        for point in reversed(coordinate_order):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= tolerance:
                upper.pop()
            upper.append(point)
        hull = lower[:-1] + upper[:-1]

        # Preserve every split vertex on the true contour so cap edges match
        # the clipped side triangulation, while excluding points inside the cap
        # introduced by earlier face triangulation.
        points: list[Point3MM] = []
        boundary_tolerance = 1e-8
        for first, second in zip(hull, hull[1:] + hull[:1]):
            dx = second[other_axes[0]] - first[other_axes[0]]
            dy = second[other_axes[1]] - first[other_axes[1]]
            length_squared = dx * dx + dy * dy
            candidates = []
            for point in all_points:
                if abs(cross(first, second, point)) > boundary_tolerance:
                    continue
                projection = (
                    (point[other_axes[0]] - first[other_axes[0]]) * dx
                    + (point[other_axes[1]] - first[other_axes[1]]) * dy
                )
                if -boundary_tolerance <= projection <= length_squared + boundary_tolerance:
                    candidates.append((projection, point))
            candidates.sort(key=lambda value: value[0])
            points.extend(point for _, point in candidates[:-1])
        center = tuple(round(
            plane_mm if index == axis else (
                sum(point[index] for point in hull) / len(hull)
            ),
            10,
        ) for index in range(3))
        desired_normal = -1.0 if keep_greater else 1.0
        cap = [
            (center, points[index], points[(index + 1) % len(points)])
            for index in range(len(points))
        ]
        if cap and _triangle_normal_raw(cap[0])[axis] * desired_normal < 0:
            cap = [(first, third, second) for first, second, third in cap]
        clipped_triangles.extend(cap)
    return tuple(clipped_triangles)


def fabrication_perimeter_bounds(model: ResolvedFabricationModel) -> tuple[float, float, float, float]:
    """Return the straight manufacturing perimeter without changing artwork."""

    correction = model.grout_gap_mm / 2.0
    if model.tile_orientation == "point_top":
        return (
            round(correction, 9), 0.0,
            round(model.artwork_width_mm - correction, 9), model.artwork_height_mm,
        )
    if model.tile_orientation == "flat_top":
        return (
            0.0, round(correction, 9),
            model.artwork_width_mm, round(model.artwork_height_mm - correction, 9),
        )
    raise ValueError(f"Unsupported fabrication orientation: {model.tile_orientation}")


def clip_mesh_to_fabrication_perimeter(
    triangles: tuple[Triangle3, ...],
    model: ResolvedFabricationModel,
) -> tuple[Triangle3, ...]:
    left, top, right, bottom = fabrication_perimeter_bounds(model)
    triangles = clip_mesh_to_axis_plane(
        triangles, axis=0, plane_mm=left, keep_greater=True,
    )
    triangles = clip_mesh_to_axis_plane(
        triangles, axis=0, plane_mm=right, keep_greater=False,
    )
    triangles = clip_mesh_to_axis_plane(
        triangles, axis=1, plane_mm=top, keep_greater=True,
    )
    return clip_mesh_to_axis_plane(
        triangles, axis=1, plane_mm=bottom, keep_greater=False,
    )


def build_single_panel_geometry(model: ResolvedFabricationModel) -> SinglePanelGeometry:
    """Generate one aligned, face-up panel; no panelization is performed."""

    profile = model.profile
    rectangle = (
        (0.0, 0.0), (model.artwork_width_mm, 0.0),
        (model.artwork_width_mm, model.artwork_height_mm),
        (0.0, model.artwork_height_mm),
    )
    base_top = profile.base_thickness_mm
    grout_top = base_top + profile.grout_thickness_mm
    bodies = [
        MeshBody(
            "panel-a1-base", "A1 Base", "base",
            clip_mesh_to_fabrication_perimeter(
                extruded_polygon_mesh(rectangle, 0.0, base_top), model,
            ),
        ),
        MeshBody(
            "panel-a1-grout-thinset", "A1 Grout/Thinset", "grout-thinset",
            (
                concave_grout_mesh(model)
                if profile.grout_surface == "concave"
                else clip_mesh_to_fabrication_perimeter(
                    extruded_polygon_mesh(rectangle, base_top, grout_top), model,
                )
            ),
        ),
    ]
    for channel in (
        value for value in model.channels if value.kind == "tile_color"
    ):
        tiles = tuple(
            value for value in model.tiles
            if value.material_channel_id == channel.channel_id
        )
        solids = tuple(
            clip_mesh_to_fabrication_perimeter(
                rounded_tile_mesh(
                    tile.full_polygon_mm, grout_top,
                    profile.straight_tile_relief_mm,
                    profile.rounded_crown_mm,
                    profile.crown_segments,
                ),
                model,
            )
            for tile in tiles
        )
        triangles = tuple(triangle for solid in solids for triangle in solid)
        bodies.append(MeshBody(
            f"panel-a1-{channel.channel_id}", f"A1 {channel.name}",
            channel.channel_id, triangles,
            tuple(value.tile_id for value in tiles),
            tuple(len(value) for value in solids),
        ))
    return SinglePanelGeometry(
        "A1", model, tuple(bodies), fabrication_perimeter_bounds(model),
    )


def triangle_normal(triangle: Triangle3) -> Point3MM:
    a, b, c = triangle
    u = tuple(b[index] - a[index] for index in range(3))
    v = tuple(c[index] - a[index] for index in range(3))
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    length = sqrt(sum(value * value for value in cross))
    if length <= 1e-10:
        raise ValueError("Fabrication mesh contains a degenerate face.")
    return tuple(value / length for value in cross)  # type: ignore[return-value]


def mesh_validation(body: MeshBody) -> dict[str, int | bool]:
    """Validate each disjoint solid in a logical body by undirected edges."""

    degenerate = 0
    nonmanifold = 0
    winding_errors = 0
    vertices: set[Point3MM] = set()
    counts = body.solid_triangle_counts or (len(body.triangles),)
    start = 0
    for count in counts:
        edge_counts: dict[tuple[Point3MM, Point3MM], int] = {}
        edge_directions: dict[
            tuple[Point3MM, Point3MM], list[tuple[Point3MM, Point3MM]]
        ] = {}
        for triangle in body.triangles[start:start + count]:
            vertices.update(triangle)
            try:
                triangle_normal(triangle)
            except ValueError:
                degenerate += 1
            for first, second in zip(triangle, triangle[1:] + triangle[:1]):
                edge = tuple(sorted((first, second)))
                edge_counts[edge] = edge_counts.get(edge, 0) + 1
                edge_directions.setdefault(edge, []).append((first, second))
        nonmanifold += sum(value != 2 for value in edge_counts.values())
        winding_errors += sum(
            len(values) == 2 and values[0] == values[1]
            for values in edge_directions.values()
        )
        start += count
    if start != len(body.triangles):
        raise ValueError("Mesh solid partition does not match triangle count.")
    return {
        "triangle_count": len(body.triangles),
        "face_count": len(body.triangles),
        "vertex_count": len(vertices),
        "degenerate_faces": degenerate,
        "nonmanifold_edges": nonmanifold,
        "winding_errors": winding_errors,
        "normals_consistent": winding_errors == 0,
        "watertight": (
            degenerate == 0 and nonmanifold == 0 and winding_errors == 0
        ),
    }


def tile_body_spatial_validation(
    body: MeshBody,
    tiles: tuple[ResolvedTile, ...],
    grout_top_z_mm: float,
    total_relief_mm: float,
    *,
    tolerance: float = 1e-7,
) -> dict:
    """Validate shell-to-source correspondence, not topology alone."""

    if len(tiles) != len(body.tile_ids):
        raise ValueError("Tile body source count does not match its tile IDs.")
    if tuple(value.tile_id for value in tiles) != body.tile_ids:
        raise ValueError("Tile body sources are not in deterministic shell order.")
    counts = body.solid_triangle_counts
    if len(counts) != len(tiles) or sum(counts) != len(body.triangles):
        raise ValueError("Tile body shell partition does not match source tiles.")
    start = 0
    shell_records = []
    errors = []
    for tile, count in zip(tiles, counts):
        triangles = body.triangles[start:start + count]
        start += count
        points = [point for triangle in triangles for point in triangle]
        source_min_x = min(value[0] for value in tile.polygon_mm)
        source_min_y = min(value[1] for value in tile.polygon_mm)
        source_max_x = max(value[0] for value in tile.polygon_mm)
        source_max_y = max(value[1] for value in tile.polygon_mm)
        bounds = (
            min(value[0] for value in points), min(value[1] for value in points),
            min(value[2] for value in points), max(value[0] for value in points),
            max(value[1] for value in points), max(value[2] for value in points),
        )
        diameter = polygon_diameter(tile.polygon_mm)
        max_edge = maximum_triangle_edge(triangles)
        shell_errors = []
        if not (
            source_min_x - tolerance <= bounds[0]
            and source_min_y - tolerance <= bounds[1]
            and bounds[3] <= source_max_x + tolerance
            and bounds[4] <= source_max_y + tolerance
        ):
            shell_errors.append("triangle vertices leave the source tile XY extent")
        if abs(bounds[2] - grout_top_z_mm) > tolerance:
            shell_errors.append("shell does not begin at the grout surface")
        if abs(bounds[5] - (grout_top_z_mm + total_relief_mm)) > tolerance:
            shell_errors.append("shell does not end at the expected crown height")
        # The polygon diameter already spans the longest legitimate footprint
        # chord. A small tolerance accounts for Z on sloped crown triangles.
        if max_edge > diameter * 1.01 + tolerance:
            shell_errors.append("triangle edge exceeds the source tile diameter guard")
        if shell_errors:
            errors.extend(f"{tile.tile_id}: {value}" for value in shell_errors)
        shell_records.append({
            "tile_id": tile.tile_id,
            "piece_type": tile.piece_type,
            "bounds_mm": list(bounds),
            "source_polygon_bounds_mm": [
                source_min_x, source_min_y, source_max_x, source_max_y,
            ],
            "source_polygon_diameter_mm": diameter,
            "maximum_triangle_edge_mm": max_edge,
            "face_count": len(triangles),
            "valid": not shell_errors,
        })
    return {
        "valid": not errors,
        "expected_shell_count": len(tiles),
        "connected_component_count": len(counts),
        "source_tile_correspondence": len(tiles) == len(counts),
        "cross_tile_triangles": 0 if not errors else None,
        "maximum_triangle_edge_mm": max(
            (value["maximum_triangle_edge_mm"] for value in shell_records),
            default=0.0,
        ),
        "shells": shell_records,
        "errors": errors,
    }

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin, sqrt

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
            clip_mesh_to_fabrication_perimeter(
                extruded_polygon_mesh(rectangle, base_top, grout_top), model,
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

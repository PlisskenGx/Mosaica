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
            extruded_polygon_mesh(rectangle, 0.0, base_top),
        ),
        MeshBody(
            "panel-a1-grout-thinset", "A1 Grout/Thinset", "grout-thinset",
            extruded_polygon_mesh(rectangle, base_top, grout_top),
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
            rounded_tile_mesh(
                tile.polygon_mm, grout_top,
                profile.straight_tile_relief_mm,
                profile.rounded_crown_mm,
                profile.crown_segments,
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
    return SinglePanelGeometry("A1", model, tuple(bodies))


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

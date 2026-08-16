from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import cos, pi, sin, sqrt
from pathlib import Path

from .project import MosaicProject


INCH_TO_MM = 25.4
_PRECISION = 9
Point = tuple[float, float]
Triangle = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]


@dataclass(frozen=True)
class PrintablePart:
    filename: str
    geometry_hash: str
    quantity: int
    piece_types: tuple[str, ...]
    palette_assignments: tuple[dict, ...]
    placements: tuple[str, ...]
    nominal_width_mm: float
    nominal_height_mm: float
    compensated_width_mm: float
    compensated_height_mm: float
    thickness_mm: float
    vertices_mm: tuple[Point, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["piece_types"] = list(self.piece_types)
        data["palette_assignments"] = list(self.palette_assignments)
        data["placements"] = list(self.placements)
        data["vertices_mm"] = [list(point) for point in self.vertices_mm]
        return data


@dataclass(frozen=True)
class PlacementPart:
    tile_id: str
    row: int
    column: int
    part_filename: str
    geometry_hash: str
    palette_index: int
    palette_id: str
    palette_name: str
    sku: str | None
    piece_type: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PrintPartsManifest:
    units: str
    thickness_mm: float
    xy_offset_mm: float
    parts: tuple[PrintablePart, ...]
    placements: tuple[PlacementPart, ...]
    summary: dict

    def to_dict(self) -> dict:
        return {
            "schema": {"name": "mosaic-engine-print-parts", "version": 1},
            "units": self.units,
            "thickness_mm": self.thickness_mm,
            "xy_offset_mm": self.xy_offset_mm,
            "parts": [part.to_dict() for part in self.parts],
            "placements": [value.to_dict() for value in self.placements],
            "summary": self.summary,
        }


def inches_to_mm(value: float) -> float:
    return value * INCH_TO_MM


def _signed_area(points: tuple[Point, ...]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )


def _canonical_polygon(points: tuple[Point, ...]) -> tuple[Point, ...]:
    if len(points) < 3:
        raise ValueError("A printable polygon must have at least three vertices.")
    values = points
    if _signed_area(values) < 0:
        values = tuple(reversed(values))
    min_x = min(x for x, _ in values)
    min_y = min(y for _, y in values)
    values = tuple(
        (
            round(x - min_x, _PRECISION) or 0.0,
            round(y - min_y, _PRECISION) or 0.0,
        )
        for x, y in values
    )
    start = min(range(len(values)), key=lambda index: values[index])
    return values[start:] + values[:start]


def _line_intersection(
    a1: Point, a2: Point, b1: Point, b2: Point,
) -> Point:
    ax, ay = a2[0] - a1[0], a2[1] - a1[1]
    bx, by = b2[0] - b1[0], b2[1] - b1[1]
    determinant = ax * by - ay * bx
    if abs(determinant) <= 1e-12:
        raise ValueError("XY offset produced parallel or invalid polygon edges.")
    cx, cy = b1[0] - a1[0], b1[1] - a1[1]
    scale = (cx * by - cy * bx) / determinant
    return a1[0] + scale * ax, a1[1] + scale * ay


def offset_polygon(points: tuple[Point, ...], offset_mm: float) -> tuple[Point, ...]:
    """Offset a convex CCW polygon; positive values expand its outline."""

    points = _canonical_polygon(points)
    shifted = []
    for first, second in zip(points, points[1:] + points[:1]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = sqrt(dx * dx + dy * dy)
        if length <= 1e-9:
            raise ValueError("Printable polygon contains a zero-length edge.")
        # For CCW winding, the right-hand normal points outside.
        nx, ny = dy / length, -dx / length
        shifted.append((
            (first[0] + nx * offset_mm, first[1] + ny * offset_mm),
            (second[0] + nx * offset_mm, second[1] + ny * offset_mm),
        ))
    result = tuple(
        _line_intersection(
            shifted[index - 1][0], shifted[index - 1][1],
            shifted[index][0], shifted[index][1],
        )
        for index in range(len(points))
    )
    for point in result:
        for first, second in shifted:
            cross = (
                (second[0] - first[0]) * (point[1] - first[1])
                - (second[1] - first[1]) * (point[0] - first[0])
            )
            if cross < -1e-7:
                raise ValueError(
                    "XY offset collapses or invalidates the printable polygon."
                )
    result = _canonical_polygon(result)
    if abs(_signed_area(result)) <= 1e-6:
        raise ValueError("XY offset collapses the printable polygon.")
    # All source pieces are convex. A sign change means an excessive inward
    # offset passed through an edge and produced an invalid outline.
    signs = []
    for index in range(len(result)):
        a, b, c = result[index - 1], result[index], result[(index + 1) % len(result)]
        cross = ((b[0] - a[0]) * (c[1] - b[1])
                 - (b[1] - a[1]) * (c[0] - b[0]))
        if abs(cross) > 1e-7:
            signs.append(cross > 0)
    if not signs or not all(signs):
        raise ValueError("XY offset collapses or invalidates the printable polygon.")
    return result


def triangulate_extrusion(points: tuple[Point, ...], thickness_mm: float) -> tuple[Triangle, ...]:
    if thickness_mm <= 0:
        raise ValueError("Tile thickness must be positive.")
    points = _canonical_polygon(points)
    bottom = tuple((x, y, 0.0) for x, y in points)
    top = tuple((x, y, thickness_mm) for x, y in points)
    triangles: list[Triangle] = []
    for index in range(1, len(points) - 1):
        triangles.append((bottom[0], bottom[index + 1], bottom[index]))
        triangles.append((top[0], top[index], top[index + 1]))
    for index in range(len(points)):
        following = (index + 1) % len(points)
        triangles.append((bottom[index], bottom[following], top[following]))
        triangles.append((bottom[index], top[following], top[index]))
    return tuple(triangles)


def _normal(triangle: Triangle) -> tuple[float, float, float]:
    a, b, c = triangle
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    length = sqrt(sum(value * value for value in cross))
    if length <= 1e-12:
        raise ValueError("Printable mesh contains a degenerate triangle.")
    return tuple(value / length for value in cross)


def write_ascii_stl(points: tuple[Point, ...], thickness_mm: float, path: str | Path) -> Path:
    path = Path(path)
    triangles = triangulate_extrusion(points, thickness_mm)
    lines = ["solid mosaic_tile"]
    for triangle in triangles:
        normal = _normal(triangle)
        lines.append("  facet normal " + " ".join(f"{value:.9g}" for value in normal))
        lines.append("    outer loop")
        for vertex in triangle:
            lines.append("      vertex " + " ".join(f"{value:.9f}" for value in vertex))
        lines.extend(("    endloop", "  endfacet"))
    lines.append("endsolid mosaic_tile")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _geometry_hash(points: tuple[Point, ...], thickness_mm: float) -> str:
    payload = json.dumps({
        "vertices_mm": _canonical_polygon(points),
        "thickness_mm": round(thickness_mm, _PRECISION),
    }, separators=(",", ":"))
    return sha256(payload.encode("ascii")).hexdigest()


def _dimensions(points: tuple[Point, ...]) -> tuple[float, float]:
    return (
        max(x for x, _ in points) - min(x for x, _ in points),
        max(y for _, y in points) - min(y for _, y in points),
    )


def _tile_id(project: MosaicProject, row: int, column: int) -> str:
    return f"placement-{row * project.columns + column:06d}"


def build_print_parts_manifest(
    project: MosaicProject,
    *,
    thickness_mm: float = 3.0,
    xy_offset_mm: float = 0.0,
) -> PrintPartsManifest:
    if thickness_mm <= 0:
        raise ValueError("Tile thickness must be positive.")
    grouped: dict[str, dict] = {}
    placement_rows = []
    for placement in project.geometry.placements:
        if placement.piece_type == "outside":
            continue
        nominal = _canonical_polygon(tuple(
            (inches_to_mm(x), inches_to_mm(y)) for x, y in placement.vertices_in
        ))
        compensated = offset_polygon(nominal, xy_offset_mm)
        geometry_hash = _geometry_hash(compensated, thickness_mm)
        palette_index = project.effective_index(placement.row, placement.column)
        color = project.palette[palette_index]
        tile_id = _tile_id(project, placement.row, placement.column)
        prefix = "full" if placement.piece_type == "full" else "cut"
        filename = f"{prefix}_{geometry_hash[:16]}.stl"
        values = grouped.setdefault(geometry_hash, {
            "filename": filename,
            "nominal": nominal,
            "compensated": compensated,
            "placements": [],
            "piece_types": set(),
            "palette_counts": {},
        })
        values["placements"].append(tile_id)
        values["piece_types"].add(placement.piece_type)
        palette_key = (palette_index, color.name, color.sku)
        values["palette_counts"][palette_key] = values["palette_counts"].get(palette_key, 0) + 1
        placement_rows.append(PlacementPart(
            tile_id=tile_id,
            row=placement.row + 1,
            column=placement.column + 1,
            part_filename=filename,
            geometry_hash=geometry_hash,
            palette_index=palette_index,
            palette_id=f"P{palette_index + 1:02d}",
            palette_name=color.name,
            sku=color.sku,
            piece_type=placement.piece_type,
        ))
    parts = []
    for geometry_hash, values in sorted(grouped.items()):
        nominal_width, nominal_height = _dimensions(values["nominal"])
        compensated_width, compensated_height = _dimensions(values["compensated"])
        palettes = tuple({
            "palette_index": key[0],
            "palette_id": f"P{key[0] + 1:02d}",
            "palette_name": key[1],
            "sku": key[2],
            "quantity": count,
        } for key, count in sorted(values["palette_counts"].items()))
        parts.append(PrintablePart(
            filename=values["filename"],
            geometry_hash=geometry_hash,
            quantity=len(values["placements"]),
            piece_types=tuple(sorted(values["piece_types"])),
            palette_assignments=palettes,
            placements=tuple(sorted(values["placements"])),
            nominal_width_mm=nominal_width,
            nominal_height_mm=nominal_height,
            compensated_width_mm=compensated_width,
            compensated_height_mm=compensated_height,
            thickness_mm=thickness_mm,
            vertices_mm=values["compensated"],
        ))
    visible = len(placement_rows)
    full = sum(value.piece_type == "full" for value in placement_rows)
    unique_cut = sum("full" not in value.piece_types for value in parts)
    return PrintPartsManifest(
        units="mm",
        thickness_mm=thickness_mm,
        xy_offset_mm=xy_offset_mm,
        parts=tuple(parts),
        placements=tuple(sorted(placement_rows, key=lambda value: (value.row, value.column))),
        summary={
            "total_physical_pieces": visible,
            "full_tile_quantity": full,
            "cut_piece_quantity": visible - full,
            "unique_part_geometries": len(parts),
            "unique_full_tile_geometries": sum("full" in value.piece_types for value in parts),
            "unique_cut_piece_geometries": unique_cut,
            "outside_placements_excluded": sum(
                value.piece_type == "outside" for value in project.geometry.placements
            ),
        },
    )


def export_print_parts_package(
    project: MosaicProject,
    output_directory: str | Path,
    *,
    thickness_mm: float = 3.0,
    xy_offset_mm: float = 0.0,
) -> dict[str, Path]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    manifest = build_print_parts_manifest(
        project, thickness_mm=thickness_mm, xy_offset_mm=xy_offset_mm,
    )
    paths: dict[str, Path] = {}
    for part in manifest.parts:
        paths[part.filename] = write_ascii_stl(
            part.vertices_mm, thickness_mm, output / part.filename,
        )
    manifest_path = output / "print_parts_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    csv_path = output / "placement_parts.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow([
            "tile_id", "row", "column", "part_filename", "geometry_hash",
            "palette_index", "palette_id", "palette_name", "sku", "piece_type",
        ])
        for value in manifest.placements:
            writer.writerow([
                value.tile_id, value.row, value.column, value.part_filename,
                value.geometry_hash, value.palette_index, value.palette_id,
                value.palette_name, value.sku or "", value.piece_type,
            ])
    paths["manifest"] = manifest_path
    paths["placement_map"] = csv_path
    return paths


def calibration_polygon(shape: str, tile_size_in: float) -> tuple[Point, ...]:
    if tile_size_in <= 0:
        raise ValueError("Calibration tile size must be positive.")
    size = inches_to_mm(tile_size_in)
    if shape == "square":
        return ((0.0, 0.0), (size, 0.0), (size, size), (0.0, size))
    if shape != "hex":
        raise ValueError(f"Unsupported calibration shape: {shape}")
    radius = size / sqrt(3.0)
    return _canonical_polygon(tuple(
        (radius * cos((30.0 + index * 60.0) * pi / 180.0),
         radius * sin((30.0 + index * 60.0) * pi / 180.0))
        for index in range(6)
    ))


def export_calibration_package(
    output_directory: str | Path,
    *,
    shape: str = "hex",
    tile_size_in: float = 1.0,
    thickness_mm: float = 3.0,
    offsets_mm: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10),
) -> dict[str, Path]:
    if thickness_mm <= 0:
        raise ValueError("Tile thickness must be positive.")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    nominal = calibration_polygon(shape, tile_size_in)
    records = []
    paths = {}
    for offset in offsets_mm:
        vertices = offset_polygon(nominal, offset)
        token = f"{offset:+.2f}".replace("+", "plus_").replace("-", "minus_").replace(".", "p")
        filename = f"calibration_{shape}_{token}mm.stl"
        geometry_hash = _geometry_hash(vertices, thickness_mm)
        paths[filename] = write_ascii_stl(vertices, thickness_mm, output / filename)
        width, height = _dimensions(vertices)
        records.append({
            "filename": filename,
            "geometry_hash": geometry_hash,
            "xy_offset_mm": offset,
            "width_mm": width,
            "height_mm": height,
            "thickness_mm": thickness_mm,
        })
    manifest_path = output / "calibration_manifest.json"
    manifest_path.write_text(json.dumps({
        "schema": {"name": "mosaic-engine-print-calibration", "version": 1},
        "shape": shape,
        "nominal_tile_size_in": tile_size_in,
        "units": "mm",
        "pieces": records,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths["manifest"] = manifest_path
    return paths

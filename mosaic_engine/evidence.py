from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from heapq import heappop, heappush
from math import atan2, degrees, hypot
from pathlib import Path

from PIL import Image

from .engine import _artwork_layout, _coverage_grid, _open_source_image
from .geometry import GridGeometry
from .model import MosaicConfig
from .processing import luminance, tile_neighbors


Coordinate = tuple[int, int]


@dataclass(frozen=True)
class TileEvidence:
    """Inspectible measurements for one full physical tile."""

    row: int
    column: int
    raw_coverage: float
    first_ring: tuple[Coordinate, ...]
    second_ring: tuple[Coordinate, ...]
    first_ring_foreground: int
    second_ring_foreground: int
    source_foreground_at_center: bool
    source_edge_distance_in: float | None
    source_boundary_orientation_deg: float | None
    source_centerline_distance_in: float | None
    local_stroke_width_in: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BWEvidence:
    """Reusable BW evidence indexed by grid coordinate."""

    tiles: dict[Coordinate, TileEvidence]
    coverage_threshold: float
    source_resolution: tuple[int, int]

    def tile(self, row: int, column: int) -> TileEvidence:
        return self.tiles[(row, column)]

    def to_dict(self) -> dict:
        return {
            "coverage_threshold": self.coverage_threshold,
            "source_resolution": list(self.source_resolution),
            "tiles": [
                tile.to_dict()
                for _, tile in sorted(self.tiles.items())
            ],
        }


def compute_project_bw_evidence(
    project,
    *,
    source_path=None,
    samples_per_axis: int = 24,
    source_analysis_width: int = 512,
) -> BWEvidence:
    """Compute evidence for a project when its source is available."""

    path = Path(source_path) if source_path is not None else project.source_path
    image = _open_source_image(path, project.config.background_rgb)
    try:
        return compute_bw_evidence(
            image,
            project.geometry,
            project.config,
            samples_per_axis=samples_per_axis,
            source_analysis_width=source_analysis_width,
        )
    finally:
        image.close()


def physical_neighbor_rings(
    row: int,
    column: int,
    geometry: GridGeometry,
    config: MosaicConfig,
) -> tuple[tuple[Coordinate, ...], tuple[Coordinate, ...]]:
    """Return active first- and exactly-second-ring neighbors."""

    active = {
        (placement.row, placement.column)
        for placement in geometry.placements
        if placement.piece_type != "outside"
    }
    origin = (row, column)
    first = {
        coordinate
        for coordinate in tile_neighbors(
            row, column, geometry.rows, geometry.columns, config
        )
        if coordinate in active
    }
    second: set[Coordinate] = set()
    for neighbor in first:
        second.update(
            coordinate
            for coordinate in tile_neighbors(
                neighbor[0],
                neighbor[1],
                geometry.rows,
                geometry.columns,
                config,
            )
            if coordinate in active
        )
    second.difference_update(first)
    second.discard(origin)
    return tuple(sorted(first)), tuple(sorted(second))


def _analysis_mask(
    image: Image.Image,
    config: MosaicConfig,
    max_width: int,
) -> tuple[list[list[bool]], int, int]:
    image = image.convert("RGB")
    width, height = image.size
    if width > max_width:
        height = max(1, round(height * max_width / width))
        width = max_width
        image = image.resize((width, height), Image.Resampling.BILINEAR)
    pixels = image.load()
    mask = []
    for y in range(height):
        row = []
        for x in range(width):
            foreground = luminance(pixels[x, y]) < config.bw_threshold
            row.append(not foreground if config.invert_bw else foreground)
        mask.append(row)
    return mask, width, height


def _boundary_pixels(mask: list[list[bool]]) -> list[Coordinate]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    result = []
    for y in range(height):
        for x in range(width):
            value = mask[y][x]
            neighbors = (
                (x - 1, y), (x + 1, y),
                (x, y - 1), (x, y + 1),
            )
            if any(
                mask[ny][nx] != value
                for nx, ny in neighbors
                if 0 <= nx < width and 0 <= ny < height
            ) or (
                value
                and any(
                    not (0 <= nx < width and 0 <= ny < height)
                    for nx, ny in neighbors
                )
            ):
                result.append((x, y))
    return result


def _distance_map(
    width: int,
    height: int,
    seeds: list[Coordinate],
    pixel_width_in: float,
    pixel_height_in: float,
) -> tuple[list[list[float]], list[list[Coordinate | None]]]:
    distances = [[float("inf")] * width for _ in range(height)]
    nearest: list[list[Coordinate | None]] = [
        [None] * width for _ in range(height)
    ]
    heap: list[tuple[float, int, int, int, int]] = []
    for x, y in seeds:
        distances[y][x] = 0.0
        nearest[y][x] = (x, y)
        heappush(heap, (0.0, x, y, x, y))
    steps = (
        (-1, 0, pixel_width_in), (1, 0, pixel_width_in),
        (0, -1, pixel_height_in), (0, 1, pixel_height_in),
        (-1, -1, hypot(pixel_width_in, pixel_height_in)),
        (-1, 1, hypot(pixel_width_in, pixel_height_in)),
        (1, -1, hypot(pixel_width_in, pixel_height_in)),
        (1, 1, hypot(pixel_width_in, pixel_height_in)),
    )
    while heap:
        distance, x, y, sx, sy = heappop(heap)
        if distance != distances[y][x]:
            continue
        for dx, dy, cost in steps:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            candidate = distance + cost
            if candidate < distances[ny][nx]:
                distances[ny][nx] = candidate
                nearest[ny][nx] = (sx, sy)
                heappush(heap, (candidate, nx, ny, sx, sy))
    return distances, nearest


def _skeleton_pixels(
    mask: list[list[bool]],
    boundary_distance: list[list[float]],
) -> list[Coordinate]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    result = []
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            if not mask[y][x] or boundary_distance[y][x] <= 0:
                continue
            value = boundary_distance[y][x]
            neighbors = [
                boundary_distance[ny][nx]
                for ny in range(y - 1, y + 2)
                for nx in range(x - 1, x + 2)
                if (nx, ny) != (x, y) and mask[ny][nx]
            ]
            if neighbors and value >= max(neighbors):
                result.append((x, y))
    return result


def _orientation(mask: list[list[bool]], x: int, y: int) -> float | None:
    height = len(mask)
    width = len(mask[0]) if height else 0
    if not (0 < x < width - 1 and 0 < y < height - 1):
        return None
    values = lambda px, py: 1.0 if mask[py][px] else 0.0
    gx = (
        values(x + 1, y - 1) + 2 * values(x + 1, y)
        + values(x + 1, y + 1) - values(x - 1, y - 1)
        - 2 * values(x - 1, y) - values(x - 1, y + 1)
    )
    gy = (
        values(x - 1, y + 1) + 2 * values(x, y + 1)
        + values(x + 1, y + 1) - values(x - 1, y - 1)
        - 2 * values(x, y - 1) - values(x + 1, y - 1)
    )
    if gx == 0 and gy == 0:
        return None
    return (degrees(atan2(gy, gx)) + 90.0) % 180.0


def compute_bw_evidence(
    image: Image.Image,
    geometry: GridGeometry,
    config: MosaicConfig,
    *,
    samples_per_axis: int = 24,
    source_analysis_width: int = 512,
) -> BWEvidence:
    """Measure BW evidence without classifying or changing the mosaic."""

    if config.quantization_mode != "bw":
        raise ValueError("BW evidence requires quantization_mode='bw'.")
    coverage = _coverage_grid(
        image, geometry, config, samples_per_axis=samples_per_axis
    )
    layout = _artwork_layout(image, geometry, config)
    mask, width, height = _analysis_mask(
        image, config, source_analysis_width
    )
    pixel_width = layout.width / width
    pixel_height = layout.height / height
    boundaries = _boundary_pixels(mask)
    boundary_distance, nearest_boundary = _distance_map(
        width, height, boundaries, pixel_width, pixel_height
    )
    skeleton = _skeleton_pixels(mask, boundary_distance)
    skeleton_distance, nearest_skeleton = _distance_map(
        width, height, skeleton, pixel_width, pixel_height
    ) if skeleton else (
        [[float("inf")] * width for _ in range(height)],
        [[None] * width for _ in range(height)],
    )
    full = {
        (placement.row, placement.column)
        for placement in geometry.placements
        if placement.piece_type == "full"
    }
    preliminary = {
        coordinate: coverage[coordinate[0]][coordinate[1]]
        >= config.coverage_threshold
        for coordinate in full
    }
    tiles = {}
    for row, column in sorted(full):
        placement = geometry.placement(row, column)
        first, second = physical_neighbor_rings(
            row, column, geometry, config
        )
        source_pixel = layout.source_pixel(
            placement.center_x_in, placement.center_y_in
        )
        edge_distance = orientation = centerline_distance = stroke_width = None
        center_foreground = False
        if source_pixel is not None:
            source_x, source_y = source_pixel
            x = min(width - 1, round(source_x * (width - 1) / max(1, image.width - 1)))
            y = min(height - 1, round(source_y * (height - 1) / max(1, image.height - 1)))
            center_foreground = mask[y][x]
            edge_distance = boundary_distance[y][x]
            boundary = nearest_boundary[y][x]
            if boundary is not None:
                orientation = _orientation(mask, *boundary)
            centerline_distance = skeleton_distance[y][x]
            centerline = nearest_skeleton[y][x]
            if centerline is not None:
                sx, sy = centerline
                radius = boundary_distance[sy][sx]
                # A zero-radius ridge is not a reliable width estimate.
                if radius > max(pixel_width, pixel_height):
                    stroke_width = 2.0 * radius
        tiles[(row, column)] = TileEvidence(
            row=row,
            column=column,
            raw_coverage=coverage[row][column],
            first_ring=first,
            second_ring=second,
            first_ring_foreground=sum(preliminary.get(c, False) for c in first),
            second_ring_foreground=sum(preliminary.get(c, False) for c in second),
            source_foreground_at_center=center_foreground,
            source_edge_distance_in=edge_distance,
            source_boundary_orientation_deg=orientation,
            source_centerline_distance_in=(
                centerline_distance if centerline_distance != float("inf") else None
            ),
            local_stroke_width_in=stroke_width,
        )
    return BWEvidence(
        tiles=tiles,
        coverage_threshold=config.coverage_threshold,
        source_resolution=(width, height),
    )

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from heapq import heappop, heappush
import hashlib
import json
from math import atan2, degrees, hypot
from pathlib import Path

from PIL import Image

from .engine import _artwork_layout, _coverage_grid, _open_source_image
from .geometry import GridGeometry
from .model import MosaicConfig
from .processing import luminance, tile_neighbors


Coordinate = tuple[int, int]
EVIDENCE_CACHE_VERSION = 1
EVIDENCE_ALGORITHM_VERSION = "bw-evidence-v1"


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

    @classmethod
    def from_dict(cls, data: dict) -> TileEvidence:
        return cls(
            row=data["row"],
            column=data["column"],
            raw_coverage=data["raw_coverage"],
            first_ring=tuple(tuple(value) for value in data["first_ring"]),
            second_ring=tuple(tuple(value) for value in data["second_ring"]),
            first_ring_foreground=data["first_ring_foreground"],
            second_ring_foreground=data["second_ring_foreground"],
            source_foreground_at_center=data["source_foreground_at_center"],
            source_edge_distance_in=data.get("source_edge_distance_in"),
            source_boundary_orientation_deg=data.get(
                "source_boundary_orientation_deg"
            ),
            source_centerline_distance_in=data.get(
                "source_centerline_distance_in"
            ),
            local_stroke_width_in=data.get("local_stroke_width_in"),
        )


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

    @classmethod
    def from_dict(cls, data: dict) -> BWEvidence:
        tiles = [TileEvidence.from_dict(value) for value in data["tiles"]]
        return cls(
            tiles={(tile.row, tile.column): tile for tile in tiles},
            coverage_threshold=data["coverage_threshold"],
            source_resolution=tuple(data["source_resolution"]),
        )


@dataclass(frozen=True)
class BWEvidenceCache:
    """Optional source-derived evidence persisted with a project."""

    input_fingerprint: str
    source_sha256: str | None
    samples_per_axis: int
    source_analysis_width: int
    evidence: BWEvidence
    format_version: int = EVIDENCE_CACHE_VERSION
    algorithm_version: str = EVIDENCE_ALGORITHM_VERSION

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "algorithm_version": self.algorithm_version,
            "input_fingerprint": self.input_fingerprint,
            "source_sha256": self.source_sha256,
            "samples_per_axis": self.samples_per_axis,
            "source_analysis_width": self.source_analysis_width,
            "evidence": self.evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> BWEvidenceCache:
        if data.get("format_version") != EVIDENCE_CACHE_VERSION:
            raise ValueError(
                "Unsupported BW evidence cache version: "
                f"{data.get('format_version')}"
            )
        if data.get("algorithm_version") != EVIDENCE_ALGORITHM_VERSION:
            raise ValueError(
                "Unsupported BW evidence algorithm version: "
                f"{data.get('algorithm_version')}"
            )
        return cls(
            input_fingerprint=data["input_fingerprint"],
            source_sha256=data.get("source_sha256"),
            samples_per_axis=data["samples_per_axis"],
            source_analysis_width=data["source_analysis_width"],
            evidence=BWEvidence.from_dict(data["evidence"]),
        )


def _fingerprint_payload(
    project,
    samples_per_axis: int,
    source_analysis_width: int,
) -> dict:
    config = project.config
    geometry = project.geometry
    return {
        "algorithm_version": EVIDENCE_ALGORITHM_VERSION,
        "samples_per_axis": samples_per_axis,
        "source_analysis_width": source_analysis_width,
        "config": {
            "tile_shape": config.tile_shape,
            "tile_width_in": config.tile_width_in,
            "tile_height_in": config.tile_height_in,
            "grout_width_in": config.grout_width_in,
            "hex_orientation": config.hex_orientation,
            "fit": config.fit,
            "artwork_inset_in": config.artwork_inset_in,
            "artwork_scale": config.artwork_scale,
            "artwork_offset_x_in": config.artwork_offset_x_in,
            "artwork_offset_y_in": config.artwork_offset_y_in,
            "quantization_mode": config.quantization_mode,
            "bw_threshold": config.bw_threshold,
            "coverage_threshold": config.coverage_threshold,
            "invert_bw": config.invert_bw,
            "background_rgb": list(config.background_rgb),
        },
        "geometry": {
            "shape": geometry.shape,
            "columns": geometry.columns,
            "rows": geometry.rows,
            "panel_bounds": asdict(geometry.panel_bounds),
            "artwork_bounds": asdict(geometry.artwork_bounds),
            "placements": [
                {
                    "row": placement.row,
                    "column": placement.column,
                    "center": [
                        placement.center_x_in,
                        placement.center_y_in,
                    ],
                    "full_vertices": [
                        list(value) for value in placement.full_vertices_in
                    ],
                    "vertices": [
                        list(value) for value in placement.vertices_in
                    ],
                    "piece_type": placement.piece_type,
                    "piece_fraction": placement.piece_fraction,
                }
                for placement in geometry.placements
            ],
        },
    }


def evidence_input_fingerprint(
    project,
    *,
    samples_per_axis: int = 24,
    source_analysis_width: int = 512,
) -> str:
    payload = json.dumps(
        _fingerprint_payload(project, samples_per_axis, source_analysis_width),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_evidence_cache(
    project,
    evidence: BWEvidence,
    *,
    source_path=None,
    samples_per_axis: int = 24,
    source_analysis_width: int = 512,
) -> BWEvidenceCache:
    path = Path(source_path) if source_path is not None else project.source_path
    return BWEvidenceCache(
        input_fingerprint=evidence_input_fingerprint(
            project,
            samples_per_axis=samples_per_axis,
            source_analysis_width=source_analysis_width,
        ),
        source_sha256=_source_sha256(path),
        samples_per_axis=samples_per_axis,
        source_analysis_width=source_analysis_width,
        evidence=evidence,
    )


def evidence_cache_validity(
    project,
    cache: BWEvidenceCache,
) -> tuple[bool, str | None]:
    expected = evidence_input_fingerprint(
        project,
        samples_per_axis=cache.samples_per_axis,
        source_analysis_width=cache.source_analysis_width,
    )
    if cache.input_fingerprint != expected:
        return False, "project geometry or evidence-affecting configuration changed"
    full = {
        (placement.row, placement.column)
        for placement in project.geometry.placements
        if placement.piece_type == "full"
    }
    if set(cache.evidence.tiles) != full:
        return False, "cached tile evidence does not match full placements"
    if cache.evidence.coverage_threshold != project.config.coverage_threshold:
        return False, "cached coverage threshold does not match the project"
    current_source_hash = _source_sha256(project.source_path)
    if (
        current_source_hash is not None
        and cache.source_sha256 is not None
        and current_source_hash != cache.source_sha256
    ):
        return False, "source artwork content changed"
    return True, None


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


def cache_project_bw_evidence(
    project,
    *,
    source_path=None,
    samples_per_axis: int = 24,
    source_analysis_width: int = 512,
) -> BWEvidence:
    """Compute and attach source-derived evidence without changing tiles."""

    evidence = compute_project_bw_evidence(
        project,
        source_path=source_path,
        samples_per_axis=samples_per_axis,
        source_analysis_width=source_analysis_width,
    )
    project.set_bw_evidence_cache(build_evidence_cache(
        project,
        evidence,
        source_path=source_path,
        samples_per_axis=samples_per_axis,
        source_analysis_width=source_analysis_width,
    ))
    return evidence


def resolve_project_bw_evidence(project) -> BWEvidence:
    """Prefer valid cached evidence, otherwise recompute from source."""

    cache = project.bw_evidence_cache
    stale_reason = None
    if cache is not None:
        valid, stale_reason = evidence_cache_validity(project, cache)
        if valid:
            return cache.evidence
    try:
        return compute_project_bw_evidence(project)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        if stale_reason is not None:
            raise RuntimeError(
                "Persisted BW evidence is stale or incompatible "
                f"({stale_reason}), and recomputation from the source failed. "
                "Restore the source artwork and run --cache-evidence again."
            ) from exc
        raise RuntimeError(
            "No valid persisted BW evidence is available and evidence could "
            "not be computed from the source. Restore the source artwork and "
            "run --cache-evidence."
        ) from exc


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

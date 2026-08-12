from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

from PIL import Image, ImageColor

from .artwork import DesignerArtwork
from .border import BorderLayerState
from .designer_colors import DesignColor, DesignerColorResolution
from .engine import _point_in_polygon
from .geometry import GridGeometry


DESIGNER_COVERAGE_THRESHOLD = 0.45
DESIGNER_SAMPLES_PER_AXIS = 24
SVG_RASTER_WIDTH = 4096
_SHAPES = {"path", "polygon", "polyline", "rect", "circle", "ellipse", "line"}
_NON_RENDERED = {"defs", "clipPath", "mask", "linearGradient", "radialGradient"}


@dataclass(frozen=True)
class GeneratedArtworkAssignment:
    tile_id: str
    row: int
    column: int
    source_rgb: tuple[int, int, int]
    color_id: str
    coverage: float

    @property
    def physical_color_id(self) -> str:
        """Compatibility alias for pre-v1.4.1 integrations."""

        return self.color_id

    def to_dict(self) -> dict:
        value = asdict(self)
        value["source_rgb"] = list(self.source_rgb)
        return value


@dataclass(frozen=True)
class DesignerGeneratedArtwork:
    revision: int
    assignments: tuple[GeneratedArtworkAssignment, ...]
    source_colors: tuple[tuple[int, int, int], ...]
    design_colors: tuple[DesignColor, ...]
    source_signature: str
    border_preset_id: str
    coverage_threshold: float = DESIGNER_COVERAGE_THRESHOLD
    samples_per_axis: int = DESIGNER_SAMPLES_PER_AXIS
    stale: bool = False
    stale_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "exists": True,
            "revision": self.revision,
            "assignments": [value.to_dict() for value in self.assignments],
            "assignment_count": len(self.assignments),
            "source_colors": [list(value) for value in self.source_colors],
            "source_color_count": len(self.source_colors),
            "design_colors": [value.to_dict() for value in self.design_colors],
            "source_signature": self.source_signature,
            "border_preset_id": self.border_preset_id,
            "coverage_threshold": self.coverage_threshold,
            "samples_per_axis": self.samples_per_axis,
            "current": not self.stale,
            "needs_regeneration": self.stale,
            "stale_reason": self.stale_reason,
        }

    @property
    def physical_colors(self) -> tuple[DesignColor, ...]:
        """Compatibility alias for pre-v1.4.1 integrations."""

        return self.design_colors


def mark_generated_stale(
    generated: DesignerGeneratedArtwork | None,
    reason: str,
) -> DesignerGeneratedArtwork | None:
    if generated is None:
        return None
    return replace(generated, stale=True, stale_reason=reason)


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _style(element: ET.Element) -> dict[str, str]:
    values = {key.lower(): value for key, value in element.attrib.items()}
    for declaration in element.attrib.get("style", "").split(";"):
        if ":" in declaration:
            name, value = declaration.split(":", 1)
            values[name.strip().lower()] = value.strip()
    return values


def _opacity(value: str | None, default: float = 1.0) -> float:
    if value is None:
        return default
    try:
        parsed = float(value[:-1]) / 100 if value.strip().endswith("%") else float(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported SVG opacity value: {value}") from exc
    return min(1.0, max(0.0, parsed))


def _paint_rgb(value: str, current_color: str) -> tuple[tuple[int, int, int], float] | None:
    normalized = value.strip()
    if normalized.lower() == "none":
        return None
    if normalized.lower() == "currentcolor":
        normalized = current_color
    if "url(" in normalized.lower():
        raise ValueError(
            "Gradient or paint-server artwork is not supported for mosaic generation yet."
        )
    try:
        red, green, blue, alpha = ImageColor.getcolor(normalized, "RGBA")
    except ValueError as exc:
        raise ValueError(f"Unsupported SVG color for mosaic generation: {value}") from exc
    return (red, green, blue), alpha / 255.0


def _declared_source_colors(svg: str) -> tuple[tuple[int, int, int], ...]:
    root = ET.fromstring(svg)
    colors: list[tuple[int, int, int]] = []

    def visit(element: ET.Element, inherited: dict[str, str], parent_opacity: float) -> None:
        tag = _local_name(element.tag)
        values = {**inherited, **_style(element)}
        if values.get("display", "").strip().lower() == "none":
            return
        if values.get("visibility", "").strip().lower() in {"hidden", "collapse"}:
            return
        opacity = parent_opacity * _opacity(values.get("opacity"))
        if opacity <= 0:
            return
        if tag == "use":
            raise ValueError(
                "SVG use references are supported for placement but not mosaic generation yet."
            )
        if tag in _NON_RENDERED:
            return
        if tag in _SHAPES:
            current = values.get("color", "black")
            fill = _paint_rgb(values.get("fill", "black"), current)
            if fill and opacity * _opacity(values.get("fill-opacity")) * fill[1] > 0:
                if fill[0] not in colors:
                    colors.append(fill[0])
            stroke = _paint_rgb(values.get("stroke", "none"), current)
            stroke_width = values.get("stroke-width", "1").strip()
            if stroke and stroke_width not in {"0", "0px", "0.0"} and (
                opacity * _opacity(values.get("stroke-opacity")) * stroke[1] > 0
            ):
                if stroke[0] not in colors:
                    colors.append(stroke[0])
        for child in element:
            visit(child, values, opacity)

    visit(root, {"fill": "black", "stroke": "none", "color": "black"}, 1.0)
    return tuple(colors)


def _ensure_macos_cairo_discovery() -> None:
    if sys.platform != "darwin" or os.environ.get("DYLD_FALLBACK_LIBRARY_PATH"):
        return
    candidates = [
        Path("/opt/homebrew/lib/libcairo.2.dylib"),
        Path("/usr/local/lib/libcairo.2.dylib"),
    ]
    for candidate in candidates:
        if candidate.exists():
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = str(candidate.parent)
            return


def _rasterize_svg(svg: str) -> Image.Image:
    _ensure_macos_cairo_discovery()
    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "SVG mosaic generation requires CairoSVG and the native Cairo library. "
            "Install both, then try Generate Mosaic again."
        ) from exc
    try:
        png = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=SVG_RASTER_WIDTH,
        )
    except Exception as exc:
        raise ValueError(f"SVG could not be rendered for mosaic generation: {exc}") from exc
    return Image.open(BytesIO(png)).convert("RGBA")


def _effective_source_colors(
    image: Image.Image,
    declared: tuple[tuple[int, int, int], ...],
) -> tuple[tuple[int, int, int], ...]:
    if not declared:
        return ()
    totals = [0 for _ in declared]
    # A rendered-pixel audit prevents unused/off-viewBox declarations from
    # consuming physical slots. Sampling every fourth pixel is deterministic.
    pixels = image.load()
    for y in range(0, image.height, 4):
        for x in range(0, image.width, 4):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                continue
            nearest = min(
                range(len(declared)),
                key=lambda index: (
                    (red - declared[index][0]) ** 2
                    + (green - declared[index][1]) ** 2
                    + (blue - declared[index][2]) ** 2,
                    index,
                ),
            )
            totals[nearest] += alpha
    return tuple(color for color, total in zip(declared, totals) if total > 0)


def _allocate_colors(
    source_colors: tuple[tuple[int, int, int], ...],
    resolution: DesignerColorResolution,
) -> tuple[dict[tuple[int, int, int], str], DesignerColorResolution]:
    updated = resolution.with_artwork_colors(source_colors)
    return {
        source: updated.color_id_for_rgb(source)
        for source in source_colors
    }, updated


def _signature(artwork: DesignerArtwork, border: BorderLayerState) -> str:
    payload = {
        "svg": artwork.sanitized_svg,
        "transform": artwork.transform.to_dict(),
        "border": border.preset_id,
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def generate_designer_artwork(
    artwork: DesignerArtwork,
    geometry: GridGeometry,
    border: BorderLayerState,
    resolution: DesignerColorResolution,
    revision: int,
) -> DesignerGeneratedArtwork:
    declared = _declared_source_colors(artwork.sanitized_svg)
    image = _rasterize_svg(artwork.sanitized_svg)
    source_colors = _effective_source_colors(image, declared)
    available = set(border.available_artwork_placement_ids)
    pixels = image.load()
    transform = artwork.transform
    sampled_assignments = []

    for index, placement in enumerate(geometry.placements):
        tile_id = f"placement-{index:06d}"
        if tile_id not in available or placement.piece_type != "full":
            continue
        polygon = placement.vertices_in
        min_x, max_x = min(x for x, _ in polygon), max(x for x, _ in polygon)
        min_y, max_y = min(y for _, y in polygon), max(y for _, y in polygon)
        inside = 0
        totals = [0.0 for _ in source_colors]
        for sample_y in range(DESIGNER_SAMPLES_PER_AXIS):
            y = min_y + (sample_y + 0.5) / DESIGNER_SAMPLES_PER_AXIS * (max_y - min_y)
            for sample_x in range(DESIGNER_SAMPLES_PER_AXIS):
                x = min_x + (sample_x + 0.5) / DESIGNER_SAMPLES_PER_AXIS * (max_x - min_x)
                if not _point_in_polygon(x, y, polygon):
                    continue
                inside += 1
                u = (x - transform.x_in) / transform.width_in
                v = (y - transform.y_in) / transform.height_in
                if not (0 <= u <= 1 and 0 <= v <= 1) or not source_colors:
                    continue
                px = min(image.width - 1, max(0, round(u * (image.width - 1))))
                py = min(image.height - 1, max(0, round(v * (image.height - 1))))
                red, green, blue, alpha = pixels[px, py]
                if alpha == 0:
                    continue
                nearest = min(
                    range(len(source_colors)),
                    key=lambda color_index: (
                        (red - source_colors[color_index][0]) ** 2
                        + (green - source_colors[color_index][1]) ** 2
                        + (blue - source_colors[color_index][2]) ** 2,
                        color_index,
                    ),
                )
                totals[nearest] += alpha / 255.0
        if not inside or not totals:
            continue
        coverages = [value / inside for value in totals]
        strongest = max(range(len(coverages)), key=lambda value: (coverages[value], -value))
        if coverages[strongest] >= DESIGNER_COVERAGE_THRESHOLD:
            source = source_colors[strongest]
            sampled_assignments.append((
                tile_id,
                placement.row,
                placement.column,
                source,
                coverages[strongest],
            ))

    used_source_colors = tuple(
        source for source in source_colors
        if any(value[3] == source for value in sampled_assignments)
    )
    mapping, updated_resolution = _allocate_colors(
        used_source_colors, resolution,
    )
    assignments = tuple(
        GeneratedArtworkAssignment(
            tile_id, row, column, source, mapping[source], coverage,
        )
        for tile_id, row, column, source, coverage in sampled_assignments
    )

    return DesignerGeneratedArtwork(
        revision=revision,
        assignments=assignments,
        source_colors=used_source_colors,
        design_colors=updated_resolution.colors,
        source_signature=_signature(artwork, border),
        border_preset_id=border.preset_id,
    )

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import isfinite
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from .border import BorderLayerState
from .geometry import GridGeometry


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
MAX_SVG_BYTES = 2_000_000
MIN_ARTWORK_SIZE_IN = 0.05
INITIAL_FIT_FRACTION = 0.80

_SAFE_ELEMENTS = {
    "svg", "g", "defs", "title", "desc",
    "path", "polygon", "polyline", "rect", "circle", "ellipse", "line",
    "clipPath", "mask", "linearGradient", "radialGradient", "stop", "use",
}
_UNSAFE_ELEMENTS = {"script", "foreignObject", "style", "image", "iframe", "object"}
_LENGTH = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(px|mm|cm|in|pt|pc)?\s*$", re.I)
_UNIT_TO_PX = {
    "": 1.0, "px": 1.0, "mm": 96.0 / 25.4, "cm": 96.0 / 2.54,
    "in": 96.0, "pt": 96.0 / 72.0, "pc": 16.0,
}


@dataclass(frozen=True)
class ArtworkTransform:
    x_in: float
    y_in: float
    width_in: float
    height_in: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DesignerArtwork:
    source_filename: str
    sanitized_svg: str
    source_view_box: tuple[float, float, float, float]
    source_aspect_ratio: float
    transform: ArtworkTransform
    initial_transform: ArtworkTransform
    selected: bool = True

    def to_dict(self) -> dict:
        return {
            "source_filename": self.source_filename,
            "sanitized_svg": self.sanitized_svg,
            "source_view_box": list(self.source_view_box),
            "source_aspect_ratio": self.source_aspect_ratio,
            "transform": self.transform.to_dict(),
            "initial_transform": self.initial_transform.to_dict(),
            "selected": self.selected,
        }


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _parse_length(value: str, label: str) -> float:
    match = _LENGTH.match(value)
    if not match:
        raise ValueError(
            f"SVG {label} must be a positive absolute length or use a viewBox."
        )
    number = float(match.group(1))
    if number <= 0:
        raise ValueError(f"SVG {label} must be positive.")
    return number * _UNIT_TO_PX[(match.group(2) or "").lower()]


def _source_view_box(root: ET.Element) -> tuple[float, float, float, float]:
    raw_view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if raw_view_box:
        try:
            values = tuple(float(value) for value in re.split(r"[\s,]+", raw_view_box.strip()))
        except ValueError as exc:
            raise ValueError("SVG viewBox must contain four finite numbers.") from exc
        if len(values) != 4 or not all(isfinite(value) for value in values):
            raise ValueError("SVG viewBox must contain four finite numbers.")
        if values[2] <= 0 or values[3] <= 0:
            raise ValueError("SVG viewBox width and height must be positive.")
        return values
    width = root.attrib.get("width")
    height = root.attrib.get("height")
    if width is None or height is None:
        raise ValueError("SVG requires a positive viewBox or absolute width and height.")
    return 0.0, 0.0, _parse_length(width, "width"), _parse_length(height, "height")


def sanitize_svg(filename: str, content: str) -> tuple[str, tuple[float, float, float, float]]:
    if Path(filename).suffix.lower() != ".svg":
        raise ValueError("Artwork must be an SVG file with a .svg extension.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("SVG artwork is empty.")
    if len(content.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError("SVG artwork exceeds the 2 MB session limit.")
    lowered = content.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError("SVG document types and entities are not supported.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed SVG artwork: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError("Uploaded artwork must have an SVG root element.")
    view_box = _source_view_box(root)

    for element in root.iter():
        tag = _local_name(element.tag)
        if tag in _UNSAFE_ELEMENTS:
            raise ValueError(f"Unsafe SVG element is not supported: {tag}")
        if tag not in _SAFE_ELEMENTS:
            raise ValueError(f"Unsupported SVG element: {tag}")
        for attribute, raw_value in element.attrib.items():
            name = _local_name(attribute)
            value = raw_value.strip()
            lower_value = value.lower().replace("\t", "").replace("\n", "")
            if name.lower().startswith("on"):
                raise ValueError(f"Unsafe SVG event handler is not supported: {name}")
            if name.lower() in {"href", "src"} and not value.startswith("#"):
                raise ValueError("SVG external resource references are not supported.")
            if any(token in lower_value for token in (
                "javascript:", "data:", "http:", "https:", "//", "expression(", "@import",
            )):
                raise ValueError("SVG executable or external resource content is not supported.")
            urls = re.findall(r"url\(([^)]+)\)", value, flags=re.I)
            if any(not item.strip(" \"'").startswith("#") for item in urls):
                raise ValueError("SVG external URL references are not supported.")

        # Browser SVG import requires namespaced elements. Normalize otherwise
        # valid namespace-free SVG documents before returning their safe form.
        if not element.tag.startswith("{"):
            element.tag = f"{{{SVG_NAMESPACE}}}{tag}"

    root.set("viewBox", " ".join(f"{value:g}" for value in view_box))
    ET.register_namespace("", SVG_NAMESPACE)
    ET.register_namespace("xlink", XLINK_NAMESPACE)
    return ET.tostring(root, encoding="unicode"), view_box


def available_artwork_bounds(
    geometry: GridGeometry,
    border: BorderLayerState,
) -> tuple[float, float, float, float]:
    indices = [
        int(tile_id.rsplit("-", 1)[-1])
        for tile_id in border.available_artwork_placement_ids
    ]
    vertices = [
        point
        for index in indices
        for point in geometry.placements[index].vertices_in
    ]
    if not vertices:
        raise ValueError("The selected Border leaves no available artwork placements.")
    xs = [point[0] for point in vertices]
    ys = [point[1] for point in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def initial_artwork_transform(
    geometry: GridGeometry,
    border: BorderLayerState,
    source_aspect_ratio: float,
) -> ArtworkTransform:
    if not isfinite(source_aspect_ratio) or source_aspect_ratio <= 0:
        raise ValueError("SVG aspect ratio must be positive and finite.")
    left, top, right, bottom = available_artwork_bounds(geometry, border)
    available_width = right - left
    available_height = bottom - top
    target_width = available_width * INITIAL_FIT_FRACTION
    target_height = available_height * INITIAL_FIT_FRACTION
    if target_width / target_height > source_aspect_ratio:
        height = target_height
        width = height * source_aspect_ratio
    else:
        width = target_width
        height = width / source_aspect_ratio
    return ArtworkTransform(
        x_in=left + (available_width - width) / 2.0,
        y_in=top + (available_height - height) / 2.0,
        width_in=width,
        height_in=height,
    )


def create_artwork(
    filename: str,
    content: str,
    geometry: GridGeometry,
    border: BorderLayerState,
) -> DesignerArtwork:
    sanitized_svg, view_box = sanitize_svg(filename, content)
    aspect_ratio = view_box[2] / view_box[3]
    transform = initial_artwork_transform(geometry, border, aspect_ratio)
    return DesignerArtwork(
        source_filename=Path(filename).name,
        sanitized_svg=sanitized_svg,
        source_view_box=view_box,
        source_aspect_ratio=aspect_ratio,
        transform=transform,
        initial_transform=transform,
    )


def update_artwork_transform(
    artwork: DesignerArtwork,
    *,
    x_in: float,
    y_in: float,
    width_in: float,
    height_in: float,
) -> DesignerArtwork:
    values = (x_in, y_in, width_in, height_in)
    if not all(isinstance(value, (int, float)) and isfinite(value) for value in values):
        raise ValueError("Artwork transform values must be finite numbers.")
    if width_in < MIN_ARTWORK_SIZE_IN or height_in < MIN_ARTWORK_SIZE_IN:
        raise ValueError(
            f"Artwork width and height must each be at least {MIN_ARTWORK_SIZE_IN:g} in."
        )
    return replace(artwork, transform=ArtworkTransform(
        float(x_in), float(y_in), float(width_in), float(height_in),
    ))


def reset_artwork(
    artwork: DesignerArtwork,
    geometry: GridGeometry,
    border: BorderLayerState,
) -> DesignerArtwork:
    transform = initial_artwork_transform(
        geometry, border, artwork.source_aspect_ratio,
    )
    return replace(artwork, transform=transform, initial_transform=transform, selected=True)

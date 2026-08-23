from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw

from .designer_export import DesignerExportSnapshot


MM_PER_INCH = 25.4
RASTER_LONG_EDGE_PX = 2400
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


@dataclass(frozen=True)
class FlatExportResult:
    path: Path
    format: str
    width_px: int | None = None
    height_px: int | None = None

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "path": str(self.path),
            "filename": self.path.name,
            "output_directory": str(self.path.parent),
            "width_px": self.width_px,
            "height_px": self.height_px,
        }


def _payload(snapshot: DesignerExportSnapshot) -> dict:
    return snapshot.project.to_dict(
        snapshot.generated_artwork, snapshot.paint_overrides,
    )


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def mosaic_svg(snapshot: DesignerExportSnapshot) -> str:
    project = _payload(snapshot)
    geometry = project["geometry"]
    width_mm = geometry["width_in"] * MM_PER_INCH
    height_mm = geometry["height_in"] * MM_PER_INCH
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{_number(width_mm)}mm",
        "height": f"{_number(height_mm)}mm",
        "viewBox": f"0 0 {_number(width_mm)} {_number(height_mm)}",
        "data-mosaica-document": snapshot.document_title,
    })
    metadata = ET.SubElement(root, f"{{{SVG_NS}}}metadata")
    metadata.text = json.dumps({
        "grout": project["grout"],
        "border_preset": project["border"]["preset_id"],
        "palette": project["color_system"]["design_colors"],
    }, sort_keys=True)
    grout = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "grout"})
    ET.SubElement(grout, f"{{{SVG_NS}}}rect", {
        "id": "finished-mosaic-perimeter", "x": "0", "y": "0",
        "width": _number(width_mm), "height": _number(height_mm),
        "fill": project["grout"]["display_color"],
    })
    ET.SubElement(root, f"{{{SVG_NS}}}g", {
        "id": "border", "data-preset": project["border"]["preset_id"],
    })
    colors = {
        value["color_id"]: value
        for value in project["color_system"]["design_colors"]
    }
    grouped: dict[str, list[dict]] = {}
    for tile in geometry["tiles"]:
        grouped.setdefault(tile["color_id"], []).append(tile)
    for palette_index, color_id in enumerate(colors, start=1):
        tiles = grouped.pop(color_id, [])
        if not tiles:
            continue
        color = colors[color_id]
        group = ET.SubElement(root, f"{{{SVG_NS}}}g", {
            "id": f"tile-color-{palette_index}",
            "data-color-id": color_id,
            "data-color-name": color["name"],
            "data-color-value": color["display_color"],
            "fill": color["display_color"],
        })
        for tile in tiles:
            ET.SubElement(group, f"{{{SVG_NS}}}polygon", {
                "id": tile["id"],
                "points": " ".join(
                    f"{_number(x * MM_PER_INCH)},{_number(y * MM_PER_INCH)}"
                    for x, y in tile["vertices_in"]
                ),
                "data-row": str(tile["row"]),
                "data-column": str(tile["column"]),
                "data-piece-type": tile["piece_type"],
                "data-border-owned": str(bool(tile["border_owned"])).lower(),
                "data-manual-override": tile["manual_override"] or "",
            })
    for color_id in sorted(grouped):
        color = colors[color_id]
        group = ET.SubElement(root, f"{{{SVG_NS}}}g", {
            "id": f"tile-color-{color_id}", "data-color-id": color_id,
            "data-color-name": color["name"],
            "data-color-value": color["display_color"],
            "fill": color["display_color"],
        })
        for tile in grouped[color_id]:
            ET.SubElement(group, f"{{{SVG_NS}}}polygon", {
                "id": tile["id"],
                "points": " ".join(
                    f"{_number(x * MM_PER_INCH)},{_number(y * MM_PER_INCH)}"
                    for x, y in tile["vertices_in"]
                ),
                "data-piece-type": tile["piece_type"],
            })
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _raster(snapshot: DesignerExportSnapshot) -> tuple[Image.Image, int, int]:
    project = _payload(snapshot)
    geometry = project["geometry"]
    aspect = geometry["width_in"] / geometry["height_in"]
    if aspect >= 1:
        width, height = RASTER_LONG_EDGE_PX, max(1, round(RASTER_LONG_EDGE_PX / aspect))
    else:
        height, width = RASTER_LONG_EDGE_PX, max(1, round(RASTER_LONG_EDGE_PX * aspect))
    scale_x = width / geometry["width_in"]
    scale_y = height / geometry["height_in"]
    image = Image.new("RGB", (width, height), project["grout"]["display_color"])
    draw = ImageDraw.Draw(image)
    for tile in geometry["tiles"]:
        draw.polygon(
            [(round(x * scale_x), round(y * scale_y)) for x, y in tile["vertices_in"]],
            fill=tile["display_color"],
        )
    return image, width, height


def export_flat_design(
    snapshot: DesignerExportSnapshot, output_path: str | Path, format_name: str,
) -> FlatExportResult:
    format_name = format_name.lower()
    if format_name not in {"svg", "png", "jpg", "jpeg"}:
        raise ValueError(f"Unsupported design export format: {format_name}")
    path = Path(output_path)
    normal_format = "jpg" if format_name == "jpeg" else format_name
    if path.suffix.lower() not in ({".jpg", ".jpeg"} if normal_format == "jpg" else {f".{normal_format}"}):
        path = path.with_suffix(f".{normal_format}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if normal_format == "svg":
        path.write_text(mosaic_svg(snapshot) + "\n", encoding="utf-8")
        return FlatExportResult(path, normal_format)
    image, width, height = _raster(snapshot)
    if normal_format == "png":
        image.save(path, format="PNG", optimize=True)
    else:
        image.save(path, format="JPEG", quality=92, subsampling=0, optimize=True)
    return FlatExportResult(path, normal_format, width, height)

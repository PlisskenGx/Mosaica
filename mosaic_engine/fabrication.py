from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from html import escape
import json
from math import ceil
from pathlib import Path

from .project import MosaicProject


@dataclass(frozen=True)
class MaterialRecord:
    palette_index: int
    palette_id: str
    palette_name: str
    abbreviation: str
    rgb_hex: str
    sku: str | None
    full_tile_count: int
    cut_piece_count: int
    visible_piece_count: int
    piece_type_counts: dict[str, int]
    equivalent_full_tile_area: float
    area_based_minimum_quantity: int
    recommended_purchase_quantity: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CutPieceRecord:
    tile_id: str
    row: int
    column: int
    palette_index: int
    palette_id: str
    palette_name: str
    sku: str | None
    piece_type: str
    piece_fraction: float
    vertices_in: tuple[tuple[float, float], ...]
    bounding_width_in: float
    bounding_height_in: float
    clipped_edges: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["vertices_in"] = [list(value) for value in self.vertices_in]
        data["clipped_edges"] = list(self.clipped_edges)
        return data


@dataclass(frozen=True)
class BuildGuidePiece:
    sequence: int
    tile_id: str
    row: int
    column: int
    palette_index: int
    palette_id: str
    abbreviation: str
    palette_name: str
    sku: str | None
    piece_type: str
    piece_fraction: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FabricationData:
    waste_factor: float
    materials: tuple[MaterialRecord, ...]
    cut_pieces: tuple[CutPieceRecord, ...]
    rows: tuple[tuple[BuildGuidePiece, ...], ...]
    summary: dict

    def to_dict(self) -> dict:
        return {
            "waste_factor": self.waste_factor,
            "materials": [value.to_dict() for value in self.materials],
            "cut_pieces": [value.to_dict() for value in self.cut_pieces],
            "rows": [
                [value.to_dict() for value in row]
                for row in self.rows
            ],
            "summary": self.summary,
        }


def _validate_waste(waste_factor: float) -> None:
    if waste_factor < 0:
        raise ValueError("Waste factor cannot be negative.")


def _tile_id(project: MosaicProject, row: int, column: int) -> str:
    return f"placement-{row * project.columns + column:06d}"


def _abbreviations(project: MosaicProject) -> tuple[str, ...]:
    used = set()
    result = []
    for index, color in enumerate(project.palette):
        letters = "".join(
            word[0] for word in color.name.upper().split() if word
        )[:3]
        if not letters:
            letters = f"P{index + 1}"
        candidate = letters
        suffix = 2
        while candidate in used:
            candidate = f"{letters}{suffix}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return tuple(result)


def _clipped_edges(project: MosaicProject, placement) -> tuple[str, ...]:
    panel = project.geometry.panel_bounds
    full = placement.full_vertices_in
    tolerance = 1e-8
    edges = []
    if min(x for x, _ in full) < panel.left - tolerance:
        edges.append("left")
    if max(x for x, _ in full) > panel.right + tolerance:
        edges.append("right")
    if min(y for _, y in full) < panel.top - tolerance:
        edges.append("top")
    if max(y for _, y in full) > panel.bottom + tolerance:
        edges.append("bottom")
    return tuple(edges)


def build_fabrication_data(
    project: MosaicProject,
    *,
    waste_factor: float = 0.10,
) -> FabricationData:
    """Build fabrication records from authoritative effective assignments."""

    _validate_waste(waste_factor)
    abbreviations = _abbreviations(project)
    material_values = [
        {
            "full": 0,
            "cut": 0,
            "types": {},
            "equivalent": 0.0,
        }
        for _ in project.palette
    ]
    cuts = []
    row_values = [[] for _ in range(project.rows)]
    total_full = total_cut = 0
    total_equivalent = 0.0

    for placement in project.geometry.placements:
        if placement.piece_type == "outside":
            continue
        row, column = placement.row, placement.column
        palette_index = project.effective_index(row, column)
        color = project.palette[palette_index]
        values = material_values[palette_index]
        values["types"][placement.piece_type] = (
            values["types"].get(placement.piece_type, 0) + 1
        )
        values["equivalent"] += placement.piece_fraction
        total_equivalent += placement.piece_fraction
        if placement.piece_type == "full":
            values["full"] += 1
            total_full += 1
        else:
            values["cut"] += 1
            total_cut += 1
            xs = [value[0] for value in placement.vertices_in]
            ys = [value[1] for value in placement.vertices_in]
            cuts.append(CutPieceRecord(
                tile_id=_tile_id(project, row, column),
                row=row + 1,
                column=column + 1,
                palette_index=palette_index,
                palette_id=f"P{palette_index + 1:02d}",
                palette_name=color.name,
                sku=color.sku,
                piece_type=placement.piece_type,
                piece_fraction=placement.piece_fraction,
                vertices_in=tuple(placement.vertices_in),
                bounding_width_in=max(xs) - min(xs),
                bounding_height_in=max(ys) - min(ys),
                clipped_edges=_clipped_edges(project, placement),
            ))
        row_values[row].append(BuildGuidePiece(
            sequence=0,
            tile_id=_tile_id(project, row, column),
            row=row + 1,
            column=column + 1,
            palette_index=palette_index,
            palette_id=f"P{palette_index + 1:02d}",
            abbreviation=abbreviations[palette_index],
            palette_name=color.name,
            sku=color.sku,
            piece_type=placement.piece_type,
            piece_fraction=placement.piece_fraction,
        ))

    rows = []
    for pieces in row_values:
        ordered = sorted(pieces, key=lambda value: value.column)
        rows.append(tuple(
            BuildGuidePiece(**{
                **asdict(piece),
                "sequence": sequence,
            })
            for sequence, piece in enumerate(ordered, 1)
        ))

    materials = []
    for index, (color, values) in enumerate(
        zip(project.palette, material_values)
    ):
        equivalent = values["equivalent"]
        area_minimum = ceil(equivalent)
        # Area plus ordinary waste, with an additional handling reserve
        # proportional to the number of distinct cuts. This avoids assuming
        # either perfect nesting or one whole purchased tile per cut piece.
        recommendation = ceil(
            equivalent * (1.0 + waste_factor)
            + values["cut"] * waste_factor
        )
        materials.append(MaterialRecord(
            palette_index=index,
            palette_id=f"P{index + 1:02d}",
            palette_name=color.name,
            abbreviation=abbreviations[index],
            rgb_hex="#%02X%02X%02X" % color.rgb,
            sku=color.sku,
            full_tile_count=values["full"],
            cut_piece_count=values["cut"],
            visible_piece_count=values["full"] + values["cut"],
            piece_type_counts=dict(sorted(values["types"].items())),
            equivalent_full_tile_area=equivalent,
            area_based_minimum_quantity=area_minimum,
            recommended_purchase_quantity=recommendation,
        ))

    summary = {
        "finished_width_in": project.physical_width_in,
        "finished_height_in": project.physical_height_in,
        "tile_shape": project.config.tile_shape,
        "hex_orientation": (
            project.config.hex_orientation
            if project.config.tile_shape == "hex"
            else None
        ),
        "nominal_tile_width_in": project.config.tile_width_in,
        "nominal_tile_height_in": project.config.tile_height_in,
        "grout_width_in": project.config.grout_width_in,
        "total_full_pieces": total_full,
        "total_cut_pieces": total_cut,
        "total_visible_pieces": total_full + total_cut,
        "outside_placements": sum(
            placement.piece_type == "outside"
            for placement in project.geometry.placements
        ),
        "total_equivalent_tile_area": total_equivalent,
        "manual_override_count": len(project.overrides),
        "source_filename": project.source_path.name,
        "source_reference": str(project.source_path),
        "palette_totals": {
            value.palette_id: {
                "name": value.palette_name,
                "sku": value.sku,
                "visible_pieces": value.visible_piece_count,
                "equivalent_full_tile_area": value.equivalent_full_tile_area,
            }
            for value in materials
        },
        "purchase_formula": (
            "ceil(equivalent_full_tile_area * (1 + waste_factor) "
            "+ cut_piece_count * waste_factor)"
        ),
        "purchase_limitation": (
            "Conservative planning estimate with an extra cut-handling "
            "reserve. No nesting or cut-piece reuse has been proven; validate "
            "stock against an actual cutting layout before purchasing."
        ),
    }
    return FabricationData(
        waste_factor=waste_factor,
        materials=tuple(materials),
        cut_pieces=tuple(sorted(cuts, key=lambda value: (value.row, value.column))),
        rows=tuple(rows),
        summary=summary,
    )


def export_material_schedule_csv(data: FabricationData, path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow([
            "palette_id", "palette_name", "abbreviation", "rgb", "sku",
            "full_tile_count", "cut_piece_count", "visible_piece_count",
            "piece_type_counts", "equivalent_full_tile_area",
            "area_based_minimum_quantity", "waste_factor",
            "recommended_purchase_quantity",
        ])
        for value in data.materials:
            writer.writerow([
                value.palette_id, value.palette_name, value.abbreviation,
                value.rgb_hex, value.sku or "", value.full_tile_count,
                value.cut_piece_count, value.visible_piece_count,
                json.dumps(value.piece_type_counts, sort_keys=True),
                f"{value.equivalent_full_tile_area:.6f}",
                value.area_based_minimum_quantity,
                f"{data.waste_factor:.4f}",
                value.recommended_purchase_quantity,
            ])
    return path


def export_cut_piece_schedule_csv(data: FabricationData, path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow([
            "tile_id", "row", "column", "palette_index", "palette_id",
            "palette_name", "sku", "piece_type", "piece_fraction",
            "bounding_width_in", "bounding_height_in", "clipped_edges",
            "vertices_in",
        ])
        for value in data.cut_pieces:
            writer.writerow([
                value.tile_id, value.row, value.column, value.palette_index,
                value.palette_id, value.palette_name, value.sku or "",
                value.piece_type, f"{value.piece_fraction:.9f}",
                f"{value.bounding_width_in:.9f}",
                f"{value.bounding_height_in:.9f}",
                ";".join(value.clipped_edges),
                ";".join(f"{x:.9f} {y:.9f}" for x, y in value.vertices_in),
            ])
    return path


def export_row_build_guide_csv(data: FabricationData, path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow([
            "row", "sequence", "column", "tile_id", "palette_id",
            "abbreviation", "palette_name", "sku", "piece_type",
            "piece_fraction",
        ])
        for row in data.rows:
            for value in row:
                writer.writerow([
                    value.row, value.sequence, value.column, value.tile_id,
                    value.palette_id, value.abbreviation, value.palette_name,
                    value.sku or "", value.piece_type,
                    f"{value.piece_fraction:.6f}",
                ])
    return path


def export_row_build_guide_text(data: FabricationData, path) -> Path:
    path = Path(path)
    lines = []
    for row_number, row in enumerate(data.rows, 1):
        pieces = " | ".join(
            f"C{value.column:02d} {value.abbreviation} "
            f"[{value.piece_type}] {value.tile_id}"
            for value in row
        ) or "(no visible pieces)"
        lines.append(f"ROW {row_number:02d}: {pieces}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_project_summary(data: FabricationData, path) -> Path:
    path = Path(path)
    summary = data.summary
    lines = [
        "MOSAIC FABRICATION SUMMARY",
        "",
        f"Finished size: {summary['finished_width_in']:.6f} x "
        f"{summary['finished_height_in']:.6f} in",
        f"Tile: {summary['tile_shape']} "
        f"{summary['nominal_tile_width_in']:.6f} x "
        f"{summary['nominal_tile_height_in']:.6f} in",
        f"Orientation: {summary['hex_orientation'] or 'n/a'}",
        f"Grout: {summary['grout_width_in']:.6f} in",
        f"Full pieces: {summary['total_full_pieces']}",
        f"Cut pieces: {summary['total_cut_pieces']}",
        f"Visible pieces: {summary['total_visible_pieces']}",
        f"Outside placements: {summary['outside_placements']}",
        f"Equivalent full-tile area: "
        f"{summary['total_equivalent_tile_area']:.6f}",
        f"Manual overrides: {summary['manual_override_count']}",
        f"Source: {summary['source_filename']} "
        f"({summary['source_reference']})",
        f"Waste factor: {data.waste_factor:.2%}",
        f"Purchase formula: {summary['purchase_formula']}",
        f"Limitation: {summary['purchase_limitation']}",
        "",
        "PALETTE TOTALS",
    ]
    for value in data.materials:
        lines.append(
            f"{value.palette_id} {value.palette_name} "
            f"SKU={value.sku or '-'} pieces={value.visible_piece_count} "
            f"equivalent={value.equivalent_full_tile_area:.6f} "
            f"purchase={value.recommended_purchase_quantity}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_assembly_map_svg(
    project: MosaicProject,
    data: FabricationData,
    path,
) -> Path:
    path = Path(path)
    panel = project.geometry.panel_bounds
    margin = max(0.5, project.config.tile_width_in * 0.75)
    legend_height = max(1.0, 0.45 * len(project.palette) + 0.7)
    view_left = panel.left - margin
    view_top = panel.top - margin
    view_width = panel.width + margin * 2
    view_height = panel.height + margin * 2 + legend_height
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{panel.width:.6f}in" height="{panel.height + legend_height:.6f}in" '
        f'viewBox="{view_left:.6f} {view_top:.6f} '
        f'{view_width:.6f} {view_height:.6f}">',
        '<rect class="page" '
        f'x="{view_left:.6f}" y="{view_top:.6f}" '
        f'width="{view_width:.6f}" height="{view_height:.6f}" fill="#f3f4f6"/>',
        f'<rect x="{panel.left:.6f}" y="{panel.top:.6f}" '
        f'width="{panel.width:.6f}" height="{panel.height:.6f}" '
        'fill="#ffffff" stroke="#222" stroke-width="0.03"/>',
        '<g id="tiles">',
    ]
    for placement in project.geometry.placements:
        if placement.piece_type == "outside":
            continue
        row, column = placement.row, placement.column
        index = project.effective_index(row, column)
        color = project.palette[index]
        tile_id = _tile_id(project, row, column)
        points = " ".join(
            f"{x:.6f},{y:.6f}" for x, y in placement.vertices_in
        )
        cut_class = " cut-piece" if placement.piece_type != "full" else ""
        lines.append(
            f'<polygon id="{tile_id}" class="tile{cut_class}" '
            f'data-row="{row + 1}" data-column="{column + 1}" '
            f'data-palette-id="P{index + 1:02d}" '
            f'points="{points}" fill="#{color.rgb[0]:02X}{color.rgb[1]:02X}'
            f'{color.rgb[2]:02X}" stroke="#68717d" stroke-width="0.018">'
            f'<title>{tile_id} · row {row + 1}, column {column + 1} · '
            f'{escape(color.name)} · {escape(placement.piece_type)}</title>'
            '</polygon>'
        )
        cx, cy = placement.visible_centroid_in
        lines.append(
            f'<text x="{cx:.6f}" y="{cy:.6f}" text-anchor="middle" '
            'dominant-baseline="central" font-size="0.105" '
            'fill="#555" pointer-events="none">'
            f'{row + 1},{column + 1}</text>'
        )
    lines.extend((
        '</g>',
        '<style>.cut-piece{stroke:#d24b26;stroke-width:.035;'
        'stroke-dasharray:.08 .04}.tile{vector-effect:non-scaling-stroke}'
        'text{font-family:Arial,sans-serif}</style>',
        f'<text x="{panel.left:.6f}" y="{panel.bottom + 0.35:.6f}" '
        'font-size="0.22" font-weight="bold">'
        f'Finished panel: {panel.width:.3f} × {panel.height:.3f} in</text>',
    ))
    legend_y = panel.bottom + 0.7
    for index, color in enumerate(project.palette):
        y = legend_y + index * 0.4
        lines.append(
            f'<rect x="{panel.left:.6f}" y="{y:.6f}" width="0.28" '
            f'height="0.28" fill="#{color.rgb[0]:02X}{color.rgb[1]:02X}'
            f'{color.rgb[2]:02X}" stroke="#555" stroke-width="0.018"/>'
            f'<text x="{panel.left + 0.4:.6f}" y="{y + 0.2:.6f}" '
            f'font-size="0.18">P{index + 1:02d} {escape(color.name)} '
            f'· {escape(color.sku or "no SKU")}</text>'
        )
    lines.append('</svg>')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_fabrication_package(
    project: MosaicProject,
    output_directory,
    *,
    waste_factor: float = 0.10,
) -> dict[str, Path]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    data = build_fabrication_data(project, waste_factor=waste_factor)
    paths = {
        "material_schedule": export_material_schedule_csv(
            data, output / "material_schedule.csv"
        ),
        "cut_piece_schedule": export_cut_piece_schedule_csv(
            data, output / "cut_piece_schedule.csv"
        ),
        "row_build_guide_csv": export_row_build_guide_csv(
            data, output / "row_build_guide.csv"
        ),
        "row_build_guide_text": export_row_build_guide_text(
            data, output / "row_build_guide.txt"
        ),
        "assembly_map": export_assembly_map_svg(
            project, data, output / "assembly_map.svg"
        ),
        "project_summary": export_project_summary(
            data, output / "project_summary.txt"
        ),
    }
    manifest = output / "fabrication_manifest.json"
    manifest.write_text(
        json.dumps(data.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    paths["manifest"] = manifest
    return paths

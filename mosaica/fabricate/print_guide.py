from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import textwrap
from typing import Any

from .panelize import PanelizationPlan


POINTS_PER_INCH = 72.0
PAGE_SIZE = (612.0, 792.0)
GUIDE_FILENAME = "Mosaica_Print_Guide.pdf"
BAMBU_CORE_WARNING = (
    "The 3mf is not from Bambu Lab, load geometry data and color data only."
)


@dataclass(frozen=True)
class PrintGuidePanel:
    panel_id: str
    row: int
    column: int
    width_mm: float
    height_mm: float
    bounds_mm: tuple[float, float, float, float]


@dataclass(frozen=True)
class PrintGuideContent:
    project_name: str
    mode_id: str
    mode_name: str
    quality_tradeoff: str
    width_mm: float
    height_mm: float
    tile_preset_id: str | None
    tile_flat_to_flat_mm: float
    tile_orientation: str
    grout_gap_mm: float
    panel_rows: int
    panel_columns: int
    panels: tuple[PrintGuidePanel, ...]
    palette: tuple[tuple[str, str, str], ...]
    part_mapping: tuple[tuple[str, str, str], ...]
    mode_instructions: tuple[tuple[str, str, str, str], ...]
    core_warning: str = BAMBU_CORE_WARNING

    @property
    def dimensions_in(self) -> tuple[float, float]:
        return (self.width_mm / 25.4, self.height_mm / 25.4)


def _project_summary(plan: PanelizationPlan, manifest: dict[str, Any]) -> dict[str, Any]:
    model = plan.model
    project = manifest.get("project")
    if isinstance(project, dict):
        return project
    return {
        "name": "Mosaica Project",
        "finished_dimensions_mm": {
            "width": model.artwork_width_mm,
            "height": model.artwork_height_mm,
        },
        "tile_system": {
            "preset_id": model.tile_preset_id,
            "flat_to_flat_mm": model.tile_flat_to_flat_mm,
            "orientation": model.tile_orientation,
            "grout_gap_mm": model.grout_gap_mm,
        },
        "palette": [
            {
                "channel_id": channel.channel_id,
                "name": channel.name,
                "display_color": channel.display_color or "#808080",
            }
            for channel in model.channels
            if channel.kind == "tile_color"
        ],
    }


def build_print_guide_content(
    plan: PanelizationPlan,
    manifest: dict[str, Any],
) -> PrintGuideContent:
    project = _project_summary(plan, manifest)
    dimensions = project["finished_dimensions_mm"]
    tile_system = project["tile_system"]
    mode = manifest["fabrication_mode"]
    instruction_records = manifest["print_guide_instructions"]
    panels_by_id = {panel.panel_id: panel for panel in plan.panels}
    manifest_ids = tuple(record["panel_id"] for record in manifest["panels"])
    if manifest_ids != tuple(panel.panel_id for panel in plan.panels):
        raise ValueError("Print guide panel order does not match the panelization plan.")
    panels = tuple(
        PrintGuidePanel(
            panel_id=record["panel_id"],
            row=panels_by_id[record["panel_id"]].row,
            column=panels_by_id[record["panel_id"]].column,
            width_mm=record["actual_dimensions_mm"]["width"],
            height_mm=record["actual_dimensions_mm"]["height"],
            bounds_mm=tuple(record["logical_artwork_bounds_mm"]),
        )
        for record in manifest["panels"]
    )
    return PrintGuideContent(
        project_name=project["name"],
        mode_id=mode["id"],
        mode_name=mode["display_name"],
        quality_tradeoff=mode["quality_tradeoff"],
        width_mm=dimensions["width"],
        height_mm=dimensions["height"],
        tile_preset_id=tile_system.get("preset_id"),
        tile_flat_to_flat_mm=tile_system["flat_to_flat_mm"],
        tile_orientation=tile_system["orientation"],
        grout_gap_mm=tile_system["grout_gap_mm"],
        panel_rows=manifest["panelization"]["final_rows"],
        panel_columns=manifest["panelization"]["final_columns"],
        panels=panels,
        palette=tuple(
            (
                item["channel_id"],
                item["name"],
                item.get("display_color") or "#808080",
            )
            for item in project["palette"]
        ),
        part_mapping=tuple(
            (
                item["user_facing_name"],
                (
                    "Base" if item["part_role"] == "base" else
                    "Grout" if item["part_role"] == "grout_thinset" else
                    item.get("project_color_name") or item.get("project_color_value")
                ),
                item.get("project_color_value") or "#808080",
            )
            for item in manifest["part_mapping"]
        ),
        mode_instructions=tuple(
            (
                item["setting"], item["tab"], item["control"], item["action"],
            )
            for item in instruction_records
        ),
    )


def _escape_pdf_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("cp1252", "replace")
        .decode("latin-1")
    )


def _hex_rgb(value: str) -> tuple[float, float, float]:
    cleaned = value.lstrip("#")
    if len(cleaned) == 8:
        cleaned = cleaned[:6]
    if len(cleaned) != 6:
        cleaned = "808080"
    return tuple(int(cleaned[index:index + 2], 16) / 255.0 for index in (0, 2, 4))


class _Page:
    def __init__(self) -> None:
        self.commands: list[str] = []

    @staticmethod
    def _pdf_y(top_y: float) -> float:
        return PAGE_SIZE[1] - top_y

    def fill(self, color: str) -> None:
        red, green, blue = _hex_rgb(color)
        self.commands.append(f"{red:.4f} {green:.4f} {blue:.4f} rg")

    def stroke(self, color: str) -> None:
        red, green, blue = _hex_rgb(color)
        self.commands.append(f"{red:.4f} {green:.4f} {blue:.4f} RG")

    def line_width(self, width: float) -> None:
        self.commands.append(f"{width:.3f} w")

    def rect(
        self, x: float, y: float, width: float, height: float,
        *, fill: str | None = None, stroke: str | None = None,
        line_width: float = 1.0,
    ) -> None:
        if fill:
            self.fill(fill)
        if stroke:
            self.stroke(stroke)
            self.line_width(line_width)
        operation = "B" if fill and stroke else "f" if fill else "S"
        self.commands.append(
            f"{x:.3f} {self._pdf_y(y + height):.3f} "
            f"{width:.3f} {height:.3f} re {operation}"
        )

    def line(
        self, x1: float, y1: float, x2: float, y2: float,
        *, color: str = "#D4D4D8", width: float = 1.0,
    ) -> None:
        self.stroke(color)
        self.line_width(width)
        self.commands.append(
            f"{x1:.3f} {self._pdf_y(y1):.3f} m "
            f"{x2:.3f} {self._pdf_y(y2):.3f} l S"
        )

    def text(
        self, x: float, y: float, value: str, *,
        size: float = 10.0, bold: bool = False, color: str = "#27272A",
    ) -> None:
        self.fill(color)
        font = "F2" if bold else "F1"
        self.commands.append(
            f"BT /{font} {size:.2f} Tf 1 0 0 1 "
            f"{x:.3f} {self._pdf_y(y):.3f} Tm "
            f"({_escape_pdf_text(value)}) Tj ET"
        )

    def wrapped_text(
        self, x: float, y: float, value: str, *,
        width: float, size: float = 10.0, leading: float | None = None,
        bold: bool = False, color: str = "#3F3F46",
    ) -> float:
        leading = leading or size * 1.35
        characters = max(10, int(width / (size * (0.58 if bold else 0.52))))
        lines: list[str] = []
        for paragraph in value.split("\n"):
            lines.extend(textwrap.wrap(
                paragraph, width=characters,
                break_long_words=False, break_on_hyphens=False,
            ) or [""])
        for line in lines:
            self.text(x, y, line, size=size, bold=bold, color=color)
            y += leading
        return y

    def stream(self) -> bytes:
        return ("\n".join(self.commands) + "\n").encode("latin-1")


def _write_pdf(pages: tuple[_Page, ...], output_path: Path, *, title: str) -> Path:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # populated after page object IDs are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    page_ids: list[int] = []
    for page in pages:
        stream = page.stream()
        content_id = len(objects) + 1
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream + b"endstream"
        )
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
    objects[1] = (
        f"<< /Type /Pages /Count {len(page_ids)} /Kids ["
        + " ".join(f"{value} 0 R" for value in page_ids)
        + "] >>"
    ).encode("ascii")
    info_id = len(objects) + 1
    objects.append(
        (
            f"<< /Title ({_escape_pdf_text(title)}) "
            "/Author (Mosaica) /Creator (Mosaica Fabricate) "
            "/Producer (Mosaica deterministic PDF writer) >>"
        ).encode("latin-1")
    )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R "
            f"/Info {info_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    return output_path


def _header(page: _Page, content: PrintGuideContent, page_number: int) -> None:
    # PDF pages are transparent unless a background is explicitly painted.
    # An opaque white page keeps dark guide text legible in native thumbnail
    # renderers as well as conventional PDF viewers.
    page.rect(0, 0, PAGE_SIZE[0], PAGE_SIZE[1], fill="#FFFFFF")
    page.rect(0, 0, PAGE_SIZE[0], 12, fill="#B35332")
    page.text(42, 48, "MOSAICA", size=11, bold=True, color="#8A3F28")
    page.text(101, 48, "by Veradura Design", size=8, color="#71717A")
    page.text(42, 69, "PRINT GUIDE", size=24, bold=True, color="#18181B")
    page.rect(458, 40, 112, 32, fill="#F4E8E2", stroke="#D6A08D")
    page.text(474, 61, f"{content.mode_name.upper()} MODE", size=10, bold=True, color="#7C3523")
    page.line(42, 86, 570, 86, color="#D4D4D8")
    page.text(42, 768, content.project_name, size=8.5, color="#71717A")
    page.text(535, 768, f"{page_number} / 3", size=8.5, color="#71717A")


def _section_title(page: _Page, y: float, title: str, subtitle: str | None = None) -> float:
    page.text(42, y, title, size=15, bold=True, color="#18181B")
    y += 18
    if subtitle:
        y = page.wrapped_text(42, y, subtitle, width=528, size=9, color="#71717A")
    return y + 8


def _draw_panel_map(page: _Page, content: PrintGuideContent, y: float) -> float:
    left, width, height = 62.0, 488.0, 250.0
    source_width = max(panel.bounds_mm[2] for panel in content.panels)
    source_height = max(panel.bounds_mm[3] for panel in content.panels)
    scale = min(width / source_width, height / source_height)
    draw_width = source_width * scale
    draw_height = source_height * scale
    origin_x = left + (width - draw_width) / 2
    origin_y = y + (height - draw_height) / 2
    page.rect(origin_x, origin_y, draw_width, draw_height, fill="#FAFAF9", stroke="#71717A", line_width=1.2)
    for panel in content.panels:
        x0, y0, x1, y1 = panel.bounds_mm
        x = origin_x + x0 * scale
        panel_y = origin_y + y0 * scale
        panel_width = (x1 - x0) * scale
        panel_height = (y1 - y0) * scale
        page.rect(
            x, panel_y, panel_width, panel_height,
            fill="#F4E8E2" if (panel.row + panel.column) % 2 == 0 else "#F7F3EF",
            stroke="#8A3F28", line_width=0.8,
        )
        label_size = 10 if min(panel_width, panel_height) >= 42 else 8
        label_x = x + max(4.0, panel_width / 2 - len(panel.panel_id) * label_size * 0.25)
        page.text(label_x, panel_y + panel_height / 2 + 3, panel.panel_id, size=label_size, bold=True, color="#592A1D")
    page.text(origin_x, origin_y - 7, "FRONT VIEW · ARTWORK FACE", size=7.5, bold=True, color="#71717A")
    return y + height + 12


def _page_one(content: PrintGuideContent) -> _Page:
    page = _Page()
    _header(page, content, 1)
    width_in, height_in = content.dimensions_in
    y = 116
    page.text(42, y, content.project_name, size=20, bold=True)
    y += 26
    page.text(
        42, y,
        f"{width_in:.1f} x {height_in:.1f} in  |  {content.width_mm:.1f} x {content.height_mm:.1f} mm",
        size=10.5, color="#52525B",
    )
    tile_preset = (content.tile_preset_id or "Custom").upper()
    page.text(
        42, y + 20,
        f"Tile {tile_preset}: {content.tile_flat_to_flat_mm:.1f} mm flat-to-flat  |  "
        f"{content.tile_orientation.replace('_', ' ').title()}  |  "
        f"{content.grout_gap_mm:.1f} mm grout",
        size=9.5, color="#52525B",
    )
    page.text(420, y, f"{len(content.panels)} PANELS", size=13, bold=True, color="#8A3F28")
    page.text(
        420, y + 20, f"{content.panel_rows} rows x {content.panel_columns} columns",
        size=9, color="#71717A",
    )
    y += 58
    y = _section_title(
        page, y, "Panel map",
        "Front view: A1 is top-left and columns increase left to right. Rear inspection reverses physical left/right; panel IDs remain attached to their front-view positions.",
    )
    y = _draw_panel_map(page, content, y)
    page.text(42, y, "EXPORTED PART / PROJECT COLOR", size=8.5, bold=True, color="#71717A")
    y += 15
    for part_name, project_color, color in content.part_mapping:
        page.rect(42, y - 8, 10, 10, fill=color, stroke="#71717A", line_width=0.5)
        page.text(60, y, part_name, size=7.5, bold=True)
        page.text(305, y, project_color, size=7.5, color="#52525B")
        if part_name.startswith("Tile "):
            page.text(470, y, color.upper(), size=7.5, color="#52525B")
        y += 14
    y += 8
    page.rect(42, y, 528, 48, fill="#F4F4F5", stroke="#D4D4D8")
    page.text(56, y + 18, "BACKSIDE PANEL ID", size=8.5, bold=True, color="#52525B")
    page.wrapped_text(
        56, y + 34,
        "Each panel carries a rear-readable debossed ID. Match the ID to this front-view map before assembly; do not arrange panels left-to-right while viewing their backs.",
        width=495, size=8.5, color="#52525B",
    )
    return page


def _numbered_step(
    page: _Page, y: float, number: int, title: str, body: str,
    *, accent: bool = False,
) -> float:
    fill = "#B35332" if accent else "#3F3F46"
    page.rect(42, y - 12, 24, 24, fill=fill)
    page.text(50, y + 5, str(number), size=10, bold=True, color="#FFFFFF")
    page.text(78, y, title, size=11, bold=True)
    return page.wrapped_text(78, y + 17, body, width=492, size=9, color="#52525B") + 9


def _page_two(content: PrintGuideContent) -> _Page:
    page = _Page()
    _header(page, content, 2)
    y = 116
    page.text(42, y, "Bambu Studio workflow", size=20, bold=True)
    y += 30
    y = _numbered_step(
        page, y, 1, "Open one panel file",
        "Open one Mosaica_<panel-id>.3mf at a time. Each file contains one object with multiple logical parts.",
    )
    y = _numbered_step(
        page, y, 2, "Continue past the Core 3MF notice",
        f'Bambu Studio may show: "{content.core_warning}" Continue; this package intentionally uses standards-compliant Core 3MF geometry and color data.',
    )
    y = _numbered_step(
        page, y, 3, "Map logical parts to filaments",
        "Use the exact exported-part mapping on page 1. Assign Base, Grout-Thinset, and each named Tile part to the intended filament. Shared colors may use the same physical filament.",
    )
    y = _numbered_step(
        page, y, 4, "Position the panel manually",
        "Place and orient the imported panel on the plate manually. Automatic Bambu plate placement is not embedded or guaranteed.",
    )
    y = _numbered_step(
        page, y, 5, "Select the validated baseline",
        "Printer: Bambu Lab P1S  |  Nozzle: 0.4 mm  |  Process: 0.20 mm Standard  |  Wall loops: 2.",
    )
    y = _numbered_step(
        page, y, 6, "Apply Adaptive Variable Layer Height",
        "Enable Adaptive Variable Layer Height manually and apply it across the complete panel. This setting is not embedded in the Core 3MF.",
    )

    y = _section_title(page, y + 2, f"{content.mode_name} mode - required actions")
    page.rect(42, y, 528, 170 if content.mode_id == "studio" else 184, fill="#FFF8F4", stroke="#D6A08D", line_width=1.0)
    action_y = y + 21
    if content.mode_id == "studio":
        actions = (
            ("Prime Tower: OFF", "Others tab > Enable > Uncheck."),
            ("Brim: No Brim", "Others tab > Brim type > Set to No Brim."),
            ("Ironing: OFF", "Leave ironing disabled."),
            (
                "Transfer risk",
                "Studio mode reclaims usable plate area for panels up to 228 x 228 mm, which can reduce panel count and print time. Physical testing accepted a small risk of minor color transfer. Disabling the Prime Tower does not disable nozzle flushing; review purge behavior in the slicer.",
            ),
        )
    else:
        actions = (
            ("Prime Tower: ON", "Keep the Prime Tower enabled."),
            ("Brim", "Use the Bambu default. No brim width is prescribed."),
            (
                "Ironing",
                "Topmost surfaces  |  Concentric  |  18% flow  |  30 mm/s  |  0.15 mm line spacing.",
            ),
            (
                "Post-slice check",
                "Museum reserves Prime Tower space with a 210 x 210 mm panel envelope, which may create more panels for maximum finish and color purity. After slicing, verify that the calculated Prime Tower, brim, and panel do not interfere. Mosaica does not position the Prime Tower.",
            ),
        )
    for title, body in actions:
        page.text(56, action_y, title, size=9.5, bold=True, color="#7C3523")
        action_y = page.wrapped_text(172, action_y, body, width=380, size=8.5, color="#52525B")
        action_y += 7
    return page


def _page_three(content: PrintGuideContent) -> _Page:
    page = _Page()
    _header(page, content, 3)
    y = 116
    page.text(42, y, "Print and assembly checklist", size=20, bold=True)
    y += 34
    slice_check = (
        "Check part assignments, purge behavior, first-layer contact, and mode-specific settings before printing."
    )
    if content.mode_id == "museum":
        slice_check += (
            " Verify after slicing that the calculated Prime Tower and panel do not interfere."
        )
    checklist = (
        ("One panel per plate", "Print each panel file on its own plate; do not combine project panels."),
        ("Artwork face up", "Keep the backside panel ID on the build plate and the artwork face upward."),
        ("Match ID to plate", "Label the physical plate or completed print with the same panel ID."),
        ("Inspect the slice", slice_check),
        ("Preserve layout", "Keep panel IDs and the panel-map arrangement together through finishing and assembly."),
    )
    for index, (title, body) in enumerate(checklist, start=1):
        y = _numbered_step(page, y, index, title, body)
    y = _section_title(page, y + 6, "Final assembly")
    page.rect(42, y, 528, 124, fill="#F4F4F5", stroke="#D4D4D8")
    assembly = (
        "Arrange panels by the artwork-space map on page 1. Adjacent panel edges follow natural grout-line seams; there are no dedicated connectors and no tile cuts.",
        "Mount the completed set to one common rigid ACP/backer so the overall artwork remains flat and aligned.",
        "Use an adhesive compatible with the selected rigid ACP/backer and printed material. This guide intentionally does not specify an adhesive product or application method.",
    )
    line_y = y + 22
    for body in assembly:
        page.text(56, line_y, "-", size=10, bold=True, color="#8A3F28")
        line_y = page.wrapped_text(70, line_y, body, width=480, size=9, color="#52525B")
        line_y += 8
    y += 148
    page.rect(42, y, 528, 72, fill="#FFF8F4", stroke="#D6A08D")
    page.text(56, y + 21, "BEFORE THE FIRST PRINT", size=9, bold=True, color="#7C3523")
    page.wrapped_text(
        56, y + 40,
        f"Confirm mode: {content.mode_name}. Confirm all {len(content.panels)} panel IDs are present, every logical part has a filament assignment, and the sliced plate matches this guide.",
        width=495, size=9, color="#52525B",
    )
    return page


def generate_print_guide(
    plan: PanelizationPlan,
    manifest: dict[str, Any] | str | Path,
    output_path: str | Path,
) -> Path:
    if isinstance(manifest, (str, Path)):
        manifest_data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    else:
        manifest_data = manifest
    content = build_print_guide_content(plan, manifest_data)
    pages = (_page_one(content), _page_two(content), _page_three(content))
    return _write_pdf(
        pages, Path(output_path),
        title=f"{content.project_name} - Mosaica Print Guide",
    )

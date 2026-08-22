from __future__ import annotations

from typing import TYPE_CHECKING

from ..border import build_border_layer
from ..print_parts import INCH_TO_MM
from ..project import MosaicProject
from .model import (
    FabricationProfile,
    LogicalMaterialChannel,
    ResolvedFabricationModel,
    ResolvedTile,
)

if TYPE_CHECKING:
    from ..designer import DesignerProjectShell
    from ..designer_generation import DesignerGeneratedArtwork


SCHEMA_NAME = "mosaica-resolved-fabrication"
SCHEMA_VERSION = 1
TILE_PROFILE = "V4 Rounded"
AUTHORITATIVE_GROUT_GAP_MM = 1.8


def _hex_orientation(value: str | None) -> str:
    if value in {"pointy", "point_top", None}:
        return "point_top"
    if value in {"flat", "flat_top"}:
        return "flat_top"
    raise ValueError(f"Unsupported fabrication tile orientation: {value}")


def _assert_grout(grout_mm: float) -> None:
    if abs(grout_mm - AUTHORITATIVE_GROUT_GAP_MM) > 1e-7:
        raise ValueError(
            "Fabricate Phase 1 requires the authoritative 1.8 mm grout gap."
        )


def _channel_id(index: int) -> str:
    return f"tile-color-{index + 1}"


def resolve_mosaic_project(
    project: MosaicProject,
    profile: FabricationProfile,
) -> ResolvedFabricationModel:
    """Freeze an editable MosaicProject into manufacturing truth."""

    grout_mm = round(project.config.grout_width_in * INCH_TO_MM, 9)
    _assert_grout(grout_mm)
    used_palette_indices = sorted({
        project.effective_index(value.row, value.column)
        for value in project.geometry.placements
        if value.piece_type != "outside"
    })
    if len(used_palette_indices) > 4:
        raise ValueError(
            "Fabricate Phase 1 supports at most four used Tile Color channels."
        )
    palette_channels = {
        palette_index: _channel_id(index)
        for index, palette_index in enumerate(used_palette_indices)
    }
    channels = [
        LogicalMaterialChannel("base", "Base", "base"),
        LogicalMaterialChannel("grout-thinset", "Grout/Thinset", "grout_thinset"),
    ]
    for index, palette_index in enumerate(used_palette_indices):
        color = project.palette[palette_index]
        channels.append(LogicalMaterialChannel(
            _channel_id(index), f"Tile Color {index + 1}", "tile_color",
            "#%02X%02X%02X" % color.rgb,
            source_color_id=f"palette-{palette_index}",
            palette_index=palette_index,
            project_color_name=color.name or None,
        ))
    tiles = tuple(
        ResolvedTile(
            tile_id=f"placement-{index:06d}",
            row=value.row,
            column=value.column,
            center_mm=(value.center_x_in * INCH_TO_MM, value.center_y_in * INCH_TO_MM),
            polygon_mm=tuple((x * INCH_TO_MM, y * INCH_TO_MM) for x, y in value.vertices_in),
            full_polygon_mm=tuple((x * INCH_TO_MM, y * INCH_TO_MM) for x, y in value.full_vertices_in),
            piece_type=value.piece_type,
            piece_fraction=value.piece_fraction,
            material_channel_id=palette_channels[project.effective_index(value.row, value.column)],
            source_color_id=f"palette-{project.effective_index(value.row, value.column)}",
        )
        for index, value in enumerate(project.geometry.placements)
        if value.piece_type != "outside"
    )
    return ResolvedFabricationModel(
        SCHEMA_NAME, SCHEMA_VERSION, profile,
        project.physical_width_in * INCH_TO_MM,
        project.physical_height_in * INCH_TO_MM,
        None,
        project.config.tile_width_in * INCH_TO_MM,
        _hex_orientation(project.config.hex_orientation),
        grout_mm, TILE_PROFILE, tiles, tuple(channels), "none",
    )


def resolve_designer_project(
    project: DesignerProjectShell,
    profile: FabricationProfile,
    *,
    generated_artwork: DesignerGeneratedArtwork | None = None,
    paint_overrides: dict[str, str] | None = None,
) -> ResolvedFabricationModel:
    """Freeze a Designer shell without retaining any browser/UI dependency."""

    _assert_grout(project.grout_mm)
    state = project.to_dict(generated_artwork, paint_overrides)
    tile_state = state["geometry"]["tiles"]
    used_color_ids = []
    for value in tile_state:
        if value["color_id"] not in used_color_ids:
            used_color_ids.append(value["color_id"])
    if len(used_color_ids) > 4:
        raise ValueError(
            "Fabricate Phase 1 supports at most four used Tile Color channels."
        )
    channel_for_color = {
        color_id: _channel_id(index)
        for index, color_id in enumerate(used_color_ids)
    }
    channels = [
        LogicalMaterialChannel("base", "Base", "base"),
        LogicalMaterialChannel(
            "grout-thinset", "Grout/Thinset", "grout_thinset",
            project.color_system.by_id(project.grout_color_id).display_color,
            project.grout_color_id,
        ),
    ]
    for index, color_id in enumerate(used_color_ids):
        color = project.color_system.by_id(color_id)
        channels.append(LogicalMaterialChannel(
            _channel_id(index), f"Tile Color {index + 1}", "tile_color",
            color.display_color, color_id, color.order, color.name or None,
        ))
    border = build_border_layer(project.geometry, project.border_preset_id)
    border_by_id = {value.tile_id: value for value in border.assignments}
    placement_by_id = {
        f"placement-{index:06d}": value
        for index, value in enumerate(project.geometry.placements)
    }
    tiles = []
    for value in tile_state:
        placement = placement_by_id[value["id"]]
        assignment = border_by_id.get(value["id"])
        tiles.append(ResolvedTile(
            tile_id=value["id"], row=value["row"], column=value["column"],
            center_mm=tuple(coordinate * INCH_TO_MM for coordinate in value["center_in"]),
            polygon_mm=tuple((x * INCH_TO_MM, y * INCH_TO_MM) for x, y in placement.vertices_in),
            full_polygon_mm=tuple((x * INCH_TO_MM, y * INCH_TO_MM) for x, y in placement.full_vertices_in),
            piece_type=value["piece_type"], piece_fraction=value["piece_fraction"],
            material_channel_id=channel_for_color[value["color_id"]],
            source_color_id=value["color_id"],
            border_owned=assignment is not None,
            border_role=assignment.color_role if assignment is not None else None,
        ))
    return ResolvedFabricationModel(
        SCHEMA_NAME, SCHEMA_VERSION, profile,
        project.geometry.width_in * INCH_TO_MM,
        project.geometry.height_in * INCH_TO_MM,
        project.tile.id, project.tile.flat_to_flat_mm,
        project.tile_orientation, project.grout_mm, TILE_PROFILE,
        tuple(tiles), tuple(channels), project.border_preset_id,
    )


def resolve_fabrication_model(
    project: MosaicProject | DesignerProjectShell,
    profile: FabricationProfile,
    **kwargs,
) -> ResolvedFabricationModel:
    if isinstance(project, MosaicProject):
        if kwargs:
            raise TypeError("MosaicProject resolution does not accept Designer state.")
        return resolve_mosaic_project(project, profile)
    if all(hasattr(project, value) for value in (
        "geometry", "tile", "color_system", "to_dict",
    )):
        return resolve_designer_project(project, profile, **kwargs)
    raise TypeError(f"Unsupported fabrication source: {type(project).__name__}")

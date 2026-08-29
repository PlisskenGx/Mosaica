"""Asymmetric front-view locks for Designer -> Fabricate orientation."""

from dataclasses import replace
from unittest.mock import patch

from PIL import Image, ImageDraw
import pytest

from mosaica.artwork import create_artwork
from mosaica.border import build_border_layer
from mosaica.designer import DesignerProjectShell
from mosaica.designer_generation import generate_designer_artwork
from mosaica.fabricate.export import parse_ascii_stl, write_mesh_stl
import mosaica.fabricate.panelize as panelize_module
from mosaica.fabricate.panelize import (
    build_panelized_fabrication, front_view_bounds, panelize_model,
)
from mosaica.fabricate.phase2b import (
    PANEL_ID_CELL_MM, PANEL_ID_DEBOSS_DEPTH_MM, PRODUCTION_PROFILE,
    PanelIdentity, _GLYPHS, _marking_cells_at, _marking_dimensions,
)
from mosaica.fabricate.resolve import resolve_designer_project
from mosaica.fabricate.three_mf import panel_plate_transform


ASYMMETRIC_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 30">'
    '<rect width="60" height="30" fill="#D00000"/>'
    '<rect x="60" width="60" height="30" fill="#0055D0"/></svg>'
)


def _normalized_marking(cells):
    left = min(x for cell in cells for x, _y in cell)
    top = min(y for cell in cells for _x, y in cell)
    return tuple(sorted(
        (round(min(x for x, _y in cell) - left, 7),
         round(min(y for _x, y in cell) - top, 7))
        for cell in cells
    ))


def _export_marking(cells, artwork_width=500.0):
    return tuple(
        tuple((artwork_width - x, y) for x, y in reversed(cell))
        for cell in cells
    )


def _asymmetric_shell_and_overrides():
    shell = DesignerProjectShell.create_custom("m", "point_top", 12, 5)
    visible = [
        (index, tile) for index, tile in enumerate(shell.geometry.placements)
        if tile.piece_type != "outside"
    ]
    left_index, left = min(visible, key=lambda item: item[1].center_x_in)
    right_index, right = max(visible, key=lambda item: item[1].center_x_in)
    return shell, {
        f"placement-{left_index:06d}": "project-color-2",
        f"placement-{right_index:06d}": "project-color-4",
    }, left, right


def test_generated_asymmetric_artwork_preserves_source_left_to_right():
    shell = DesignerProjectShell.create_custom("m", "point_top", 12, 5)
    border = build_border_layer(shell.geometry, "none")
    artwork = create_artwork("left-red-right-blue.svg", ASYMMETRIC_SVG, shell.geometry, border)
    raster = Image.new("RGBA", (120, 30), (208, 0, 0, 255))
    ImageDraw.Draw(raster).rectangle((60, 0, 119, 29), fill=(0, 85, 208, 255))
    with patch("mosaica.designer_generation._rasterize_svg", return_value=raster):
        generated = generate_designer_artwork(
            artwork, shell.geometry, border, shell.color_system, 1,
        )
    placement_by_id = {
        f"placement-{index:06d}": tile
        for index, tile in enumerate(shell.geometry.placements)
    }
    red_x = [
        placement_by_id[item.tile_id].center_x_in
        for item in generated.assignments if item.source_rgb == (208, 0, 0)
    ]
    blue_x = [
        placement_by_id[item.tile_id].center_x_in
        for item in generated.assignments if item.source_rgb == (0, 85, 208)
    ]
    assert red_x and blue_x
    assert max(red_x) < min(blue_x)
    model = resolve_designer_project(
        shell, PRODUCTION_PROFILE, generated_artwork=generated,
    )
    red_color = next(
        item.color_id for item in generated.assignments
        if item.source_rgb == (208, 0, 0)
    )
    blue_color = next(
        item.color_id for item in generated.assignments
        if item.source_rgb == (0, 85, 208)
    )
    red_resolved_x = [
        tile.center_mm[0] for tile in model.tiles
        if tile.source_color_id == red_color
    ]
    blue_resolved_x = [
        tile.center_mm[0] for tile in model.tiles
        if tile.source_color_id == blue_color
    ]
    # Resolution remains authoritative Designer/front space. Conversion is
    # deliberately deferred until validated bodies have been constructed.
    assert max(red_resolved_x) < min(blue_resolved_x)
    plan = panelize_model(model, mode="studio")
    ownership = dict(plan.tile_ownership)
    red_tile = min(
        (tile for tile in model.tiles if tile.source_color_id == red_color),
        key=lambda tile: tile.center_mm[0],
    )
    assert ownership[red_tile.tile_id] == "A1"


def test_front_view_order_survives_resolution_panelization_mesh_and_stl(tmp_path):
    shell, overrides, designer_left, designer_right = _asymmetric_shell_and_overrides()
    model = resolve_designer_project(
        shell, PRODUCTION_PROFILE, paint_overrides=overrides,
    )
    left = next(tile for tile in model.tiles if tile.source_color_id == "project-color-2")
    right = next(tile for tile in model.tiles if tile.source_color_id == "project-color-4")
    assert left.center_mm[0] < right.center_mm[0]
    assert left.center_mm[0] == pytest.approx(designer_left.center_x_in * 25.4)
    assert right.center_mm[0] == pytest.approx(designer_right.center_x_in * 25.4)

    plan = panelize_model(model, mode="studio")
    ownership = dict(plan.tile_ownership)
    assert plan.panels[0].panel_id == "A1"
    assert (plan.panels[0].row, plan.panels[0].column) == (0, 0)
    assert plan.panels[0].bounds_mm[0] > plan.panels[-1].bounds_mm[0]
    assert ownership[left.tile_id] == "A1"
    assert ownership[right.tile_id] == plan.panels[-1].panel_id

    fabrication = build_panelized_fabrication(plan)
    left_panel = next(panel for panel in fabrication.panels if panel.panel_id == ownership[left.tile_id])
    right_panel = next(panel for panel in fabrication.panels if panel.panel_id == ownership[right.tile_id])
    left_body = next(body for body in left_panel.bodies if left.tile_id in body.tile_ids)
    right_body = next(body for body in right_panel.bodies if right.tile_id in body.tile_ids)
    assert left_body.bounds_mm[0] > right_body.bounds_mm[0]
    parsed_left = replace(left_body, triangles=parse_ascii_stl(
        write_mesh_stl(left_body, tmp_path / "left.stl")
    ))
    parsed_right = replace(right_body, triangles=parse_ascii_stl(
        write_mesh_stl(right_body, tmp_path / "right.stl")
    ))
    assert parsed_left.bounds_mm[0] > parsed_right.bounds_mm[0]


def test_build_plate_transforms_rotate_or_translate_but_never_reflect():
    bounds = (4.0, 8.0, 104.0, 188.0)
    for rotation in (0, 90):
        transform = panel_plate_transform(bounds, rotation)
        determinant_xy = (
            transform[0] * transform[4] - transform[1] * transform[3]
        )
        assert determinant_xy == 1.0


def test_asymmetric_l_marker_survives_even_custom_grid_physical_front_projection():
    shell = DesignerProjectShell.create_custom("m", "point_top", 4, 4)
    marker_color_id = "project-color-4"
    tiles = shell.to_dict()["geometry"]["tiles"]
    rows = {}
    for tile in tiles:
        rows.setdefault(round(tile["center_in"][1], 9), []).append(tile)
    ordered_rows = [
        sorted(row, key=lambda tile: tile["center_in"][0])
        for _y, row in sorted(rows.items())
    ]
    selected = {
        ordered_rows[1][1]["id"], ordered_rows[2][1]["id"],
        ordered_rows[3][1]["id"], ordered_rows[3][2]["id"],
    }
    expected = {
        tuple(round(value * 25.4, 7) for value in tile["center_in"])
        for tile in tiles if tile["id"] in selected
    }
    model = resolve_designer_project(
        shell, PRODUCTION_PROFILE,
        paint_overrides={tile_id: marker_color_id for tile_id in selected},
    )
    physical = {
        (
            round(tile.center_mm[0], 7),
            round(tile.center_mm[1], 7),
        )
        for tile in model.tiles if tile.source_color_id == marker_color_id
    }
    assert physical == expected


def test_panel_ids_and_bounds_are_front_view_even_though_export_x_is_reversed():
    shell, overrides, _left, _right = _asymmetric_shell_and_overrides()
    model = resolve_designer_project(
        shell, PRODUCTION_PROFILE, paint_overrides=overrides,
    )
    plan = panelize_model(model, mode="studio")
    assert [panel.panel_id for panel in plan.panels] == ["A1", "A2"]
    assert plan.panels[0].bounds_mm[0] > plan.panels[1].bounds_mm[0]
    assert front_view_bounds(model, plan.panels[0].bounds_mm)[0] < (
        front_view_bounds(model, plan.panels[1].bounds_mm)[0]
    )
    assert dict(plan.panels[0].neighbors) == {"right": "A2"}
    assert dict(plan.panels[1].neighbors) == {"left": "A1"}


def test_backside_glyph_mirror_is_local_and_does_not_change_panel_identity():
    cells = _marking_cells_at(PanelIdentity("A1", 0, 0), 10.0, 20.0)
    assert cells
    assert PanelIdentity("A1", 0, 0).panel_id == "A1"
    assert len(cells) == sum(
        character == "1"
        for glyph in (_GLYPHS["A"], _GLYPHS["1"])
        for row in glyph for character in row
    )
    # The front-coordinate top row has the three A pixels on the right;
    # physical rear viewing reverses that local X axis back to readable A1.
    top = min(y for cell in cells for _x, y in cell)
    offsets = {
        round(min(x for x, _y in cell) - min(x for cell in cells for x, _y in cell), 2)
        for cell in cells if min(y for _x, y in cell) == top
    }
    assert {8.25, 9.7, 11.15}.issubset(offsets)


@pytest.mark.parametrize("panel_id", ("B7", "C2", "E7", "F9"))
def test_complete_rear_id_is_locally_reflected_once_before_global_export(panel_id):
    identity = PanelIdentity(panel_id, 1, int(panel_id[1:]) - 1)
    normal = _marking_cells_at(identity, 20.0, 30.0, mirror_x=False)
    local_rear = _marking_cells_at(identity, 20.0, 30.0)
    exported = _export_marking(local_rear)

    assert _normalized_marking(local_rear) != _normalized_marking(normal)
    assert _normalized_marking(exported) == _normalized_marking(normal)
    assert identity.panel_id == panel_id
    assert len(local_rear) == len(normal)
    for cells in (normal, local_rear):
        bounds = (
            max(x for cell in cells for x, _y in cell)
            - min(x for cell in cells for x, _y in cell),
            max(y for cell in cells for _x, y in cell)
            - min(y for cell in cells for _x, y in cell),
        )
        assert bounds == pytest.approx(_marking_dimensions(panel_id))


def test_rear_b7_composition_preserves_character_order_and_physical_specification():
    identity = PanelIdentity("B7", 1, 6)
    normal = _marking_cells_at(identity, 0.0, 0.0, mirror_x=False)
    exported = _export_marking(_marking_cells_at(identity, 0.0, 0.0))
    assert _normalized_marking(exported) == _normalized_marking(normal)
    assert len(normal) == sum(
        value == "1"
        for glyph in (_GLYPHS["B"], _GLYPHS["7"])
        for row in glyph for value in row
    )
    assert PANEL_ID_CELL_MM == 1.0
    assert PANEL_ID_DEBOSS_DEPTH_MM == 0.35
    assert _marking_dimensions("B7")[0] > _marking_dimensions("B")[0]


def test_rear_fix_changes_only_base_deboss_geometry():
    shell, overrides, _left, _right = _asymmetric_shell_and_overrides()
    model = resolve_designer_project(
        shell, PRODUCTION_PROFILE, paint_overrides=overrides,
    )
    plan = panelize_model(model, mode="studio")
    corrected = build_panelized_fabrication(plan)
    original_marking = panelize_module._marking_for_panel

    def uncorrected_marking(panel, cells, tiles):
        marking = original_marking(panel, cells, tiles)
        return _marking_cells_at(
            PanelIdentity(panel.panel_id, panel.row, panel.column),
            min(x for cell in marking for x, _y in cell),
            min(y for cell in marking for _x, y in cell),
            mirror_x=False,
        )

    with patch(
        "mosaica.fabricate.panelize._marking_for_panel",
        side_effect=uncorrected_marking,
    ):
        uncorrected = build_panelized_fabrication(plan)

    assert corrected.plan == uncorrected.plan
    for corrected_panel, old_panel in zip(
        corrected.panels, uncorrected.panels, strict=True,
    ):
        assert corrected_panel.fabrication_bounds_mm == old_panel.fabrication_bounds_mm
        corrected_bodies = {
            body.material_channel_id: body for body in corrected_panel.bodies
        }
        old_bodies = {body.material_channel_id: body for body in old_panel.bodies}
        assert corrected_bodies.keys() == old_bodies.keys()
        for channel_id in corrected_bodies:
            new_body, old_body = corrected_bodies[channel_id], old_bodies[channel_id]
            assert new_body.bounds_mm == old_body.bounds_mm
            assert len(new_body.triangles) == len(old_body.triangles)
            if channel_id == "base":
                assert new_body.triangles != old_body.triangles
            else:
                assert new_body == old_body

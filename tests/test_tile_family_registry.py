from dataclasses import replace

import pytest

from mosaica.designer import DESIGNER_GROUT_MM, DesignerProjectShell, TILE_PRESETS
from mosaica.geometry import custom_counted_hex_geometry, vertex_constrained_panel_hex_geometry
from mosaica.model import MosaicConfig
from mosaica.processing import tile_neighbors
from mosaica.tiles import TileSystemSelection, get_tile_family, production_tile_families, resolve_tile_system


def _config(preset_id="m", orientation="point_top"):
    preset = get_tile_family().preset(preset_id)
    return MosaicConfig(
        tile_shape="hex", tile_width_in=preset.flat_to_flat_in,
        tile_height_in=preset.flat_to_flat_in,
        grout_width_in=DESIGNER_GROUT_MM / 25.4,
        hex_orientation=orientation,
    )


def test_registry_contains_designer_hexagon_and_square_and_rejects_unknown_family():
    families = production_tile_families()
    assert [(value.id, value.display_name) for value in families] == [
        ("hexagon", "Hexagon"), ("square", "Square"),
    ]
    assert get_tile_family() is get_tile_family("hexagon")
    assert get_tile_family("square").display_name == "Square"


def test_hexagon_metadata_owns_current_orientations_and_presets():
    family = get_tile_family()
    assert [(value.id, value.display_name) for value in family.orientations()] == [
        ("point_top", "Point Top"), ("flat_top", "Flat Top")
    ]
    assert [value.id for value in family.presets()] == ["s", "m", "l"]
    assert [value.primary_dimension_mm for value in family.presets()] == [20.0, 24.0, 28.0]
    assert all(value.dimension_kind == "flat_to_flat" for value in family.presets())
    assert TILE_PRESETS is family.presets()


def test_tile_system_selection_validates_through_family():
    family, selection = resolve_tile_system(TileSystemSelection("hexagon", "flat_top", "m"))
    assert family.id == "hexagon"
    assert selection == TileSystemSelection("hexagon", "flat_top", "m")
    with pytest.raises(ValueError, match="Unsupported Hexagon orientation"):
        family.selection("diagonal", "m")
    with pytest.raises(ValueError, match="Unknown Hexagon tile preset"):
        family.selection("point_top", "xl")


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_preset_panel_delegates_to_existing_hex_geometry(orientation):
    family = get_tile_family()
    config = replace(_config(orientation=orientation), target_width_in=24.0, target_height_in=36.0)
    assert family.build_preset_panel("m", orientation, DESIGNER_GROUT_MM, 24.0, 36.0) == (
        vertex_constrained_panel_hex_geometry(config, 24.0, 36.0)
    )


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_custom_grid_delegates_to_existing_hex_geometry(orientation):
    family = get_tile_family()
    assert family.build_custom_grid("m", orientation, DESIGNER_GROUT_MM, 6, 4) == (
        custom_counted_hex_geometry(_config(orientation=orientation), 6, 4)
    )


@pytest.mark.parametrize("orientation,legacy", (("point_top", "pointy"), ("flat_top", "flat")))
def test_hexagon_neighbors_delegate_to_existing_adjacency(orientation, legacy):
    family = get_tile_family()
    config = MosaicConfig(tile_shape="hex", hex_orientation=legacy)
    assert family.neighbors(2, 3, 6, 8, orientation) == tile_neighbors(2, 3, 6, 8, config)


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_designer_creation_matches_direct_hex_paths(orientation):
    shell = DesignerProjectShell.create("square", "m", orientation)
    config = replace(_config(orientation=orientation), target_width_in=24.0, target_height_in=24.0)
    assert shell.geometry == vertex_constrained_panel_hex_geometry(config, 24.0, 24.0)
    custom = DesignerProjectShell.create_custom("m", orientation, 5, 4)
    assert custom.geometry == custom_counted_hex_geometry(_config(orientation=orientation), 5, 4)
    assert shell.to_dict()["tile_shape"] == "hexagon"

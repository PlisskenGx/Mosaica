from dataclasses import replace
from types import SimpleNamespace

import pytest

from mosaica.designer import DesignerProjectShell
from mosaica.fabricate import (
    FabricationProfile,
    HEXAGON_FABRICATION_STRATEGY,
    ResolvedTileSystem,
    fabrication_perimeter_bounds,
    get_tile_fabrication_strategy,
    resolve_designer_project,
)
from mosaica.fabricate.mesh import _hexagon_fabrication_perimeter_bounds
from mosaica.tiles import TileSystemSelection


PROFILE = FabricationProfile("t5-family-resolution", 1, 1.4, 0.8)


@pytest.mark.parametrize("preset_id,dimension_mm", (("s", 20.0), ("m", 24.0), ("l", 28.0)))
@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_designer_resolves_one_authoritative_hex_tile_system(
    preset_id, dimension_mm, orientation,
):
    shell = DesignerProjectShell.create_custom(preset_id, orientation, 3, 3)
    model = resolve_designer_project(shell, PROFILE)

    assert model.tile_system == ResolvedTileSystem(
        family_id="hexagon",
        family_display_name="Hexagon",
        preset_id=preset_id,
        orientation_id=orientation,
        primary_dimension=replace(
            model.tile_system.primary_dimension,
            kind="flat_to_flat",
            value_mm=dimension_mm,
        ),
        profile_id="V4 Rounded",
    )
    assert model.tile_strategy is HEXAGON_FABRICATION_STRATEGY
    assert model.tile_preset_id == preset_id
    assert model.tile_flat_to_flat_mm == dimension_mm
    assert model.tile_orientation == orientation
    assert model.tile_profile == "V4 Rounded"


def test_legacy_output_contract_is_derived_from_tile_system():
    model = resolve_designer_project(
        DesignerProjectShell.create_custom("m", "flat_top", 3, 3), PROFILE,
    )

    assert model.to_dict()["tile_system"] == {
        "preset_id": "m",
        "flat_to_flat_mm": 24.0,
        "orientation": "flat_top",
        "grout_gap_mm": 1.8,
        "profile": "V4 Rounded",
    }


def test_family_registry_is_consulted_before_designer_geometry_state():
    unsupported = SimpleNamespace(
        tile_system=TileSystemSelection("square", "straight", "m"),
        grout_mm=1.8,
    )

    with pytest.raises(
        ValueError, match="Unsupported production fabrication family: square",
    ):
        resolve_designer_project(unsupported, PROFILE)


@pytest.mark.parametrize(
    "selection,message",
    (
        (TileSystemSelection("hexagon", "diagonal", "m"), "Unsupported Hexagon orientation"),
        (TileSystemSelection("hexagon", "point_top", "xl"), "Unknown Hexagon tile preset"),
    ),
)
def test_family_rejects_invalid_orientation_or_preset_before_geometry(
    selection, message,
):
    invalid = SimpleNamespace(tile_system=selection, grout_mm=1.8)
    with pytest.raises(ValueError, match=message):
        resolve_designer_project(invalid, PROFILE)


def test_strategy_and_resolved_family_cannot_disagree():
    model = resolve_designer_project(
        DesignerProjectShell.create_custom("m", "point_top", 3, 3), PROFILE,
    )
    mismatched = replace(model.tile_system, family_id="other")

    with pytest.raises(ValueError, match="strategy does not match"):
        replace(model, tile_system=mismatched)


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_hex_strategy_delegates_to_exact_existing_perimeter(orientation):
    model = resolve_designer_project(
        DesignerProjectShell.create_custom("m", orientation, 4, 4), PROFILE,
    )

    assert fabrication_perimeter_bounds(model) == _hexagon_fabrication_perimeter_bounds(model)


def test_fabrication_strategy_registry_contains_hex_only():
    assert get_tile_fabrication_strategy("hexagon") is HEXAGON_FABRICATION_STRATEGY
    with pytest.raises(ValueError, match="Unsupported production fabrication family: square"):
        get_tile_fabrication_strategy("square")

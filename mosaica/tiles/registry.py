"""Deterministic production tile-family registry."""

from types import MappingProxyType

from .hexagon import HEXAGON_TILE_FAMILY
from .types import TileSystemSelection

DEFAULT_TILE_FAMILY_ID = "hexagon"
_PRODUCTION_TILE_FAMILIES = MappingProxyType({HEXAGON_TILE_FAMILY.id: HEXAGON_TILE_FAMILY})
_GEOMETRY_SHAPE_FAMILIES = MappingProxyType({"hex": HEXAGON_TILE_FAMILY})


def production_tile_families(): return tuple(_PRODUCTION_TILE_FAMILIES.values())


def get_tile_family(family_id=DEFAULT_TILE_FAMILY_ID):
    try:
        return _PRODUCTION_TILE_FAMILIES[family_id]
    except KeyError as exc:
        raise ValueError(f"Unknown production tile family: {family_id}") from exc


def get_tile_family_for_geometry_shape(shape_id):
    """Resolve legacy geometry shape IDs without changing persisted schemas."""
    try:
        return _GEOMETRY_SHAPE_FAMILIES[shape_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported production geometry shape: {shape_id}") from exc


def resolve_tile_system(selection: TileSystemSelection):
    family = get_tile_family(selection.family_id)
    return family, family.selection(selection.orientation_id, selection.preset_id)

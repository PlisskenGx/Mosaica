"""Hexagon-only production tile-family ownership and static dispatch."""

from .hexagon import HEXAGON_ORIENTATIONS, HEXAGON_PRESETS, HexagonTileFamily
from .registry import DEFAULT_TILE_FAMILY_ID, get_tile_family, production_tile_families, resolve_tile_system
from .types import TileFamily, TileOrientationSpec, TileSizePreset, TileSystemSelection

__all__ = [
    "DEFAULT_TILE_FAMILY_ID", "HEXAGON_ORIENTATIONS", "HEXAGON_PRESETS",
    "HexagonTileFamily", "TileFamily", "TileOrientationSpec", "TileSizePreset",
    "TileSystemSelection", "get_tile_family", "production_tile_families",
    "resolve_tile_system",
]

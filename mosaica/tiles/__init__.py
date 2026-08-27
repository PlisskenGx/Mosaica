"""Hexagon-only production tile-family ownership and static dispatch."""

from .hexagon import (
    HEXAGON_BORDER_PRESET_IDS, HEXAGON_ORIENTATIONS, HEXAGON_PRESETS,
    HEXAGON_TOPOLOGY, HexagonTileFamily, HexagonTopology,
)
from .registry import (
    DEFAULT_TILE_FAMILY_ID, get_tile_family, get_tile_family_for_geometry_shape,
    production_tile_families, resolve_tile_system,
)
from .types import (
    TileFamily, TileOrientationSpec, TileSizePreset, TileSystemSelection,
    TileTopology,
)

__all__ = [
    "DEFAULT_TILE_FAMILY_ID", "HEXAGON_BORDER_PRESET_IDS",
    "HEXAGON_ORIENTATIONS", "HEXAGON_PRESETS", "HEXAGON_TOPOLOGY",
    "HexagonTileFamily", "HexagonTopology", "TileFamily",
    "TileOrientationSpec", "TileSizePreset", "TileSystemSelection",
    "TileTopology", "get_tile_family", "get_tile_family_for_geometry_shape",
    "production_tile_families", "resolve_tile_system",
]

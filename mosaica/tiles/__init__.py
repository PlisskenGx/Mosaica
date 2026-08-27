"""Static Designer tile-family ownership and dispatch."""

from .hexagon import (
    HEXAGON_BORDER_PRESET_IDS, HEXAGON_ORIENTATIONS, HEXAGON_PRESETS,
    HEXAGON_TOPOLOGY, HexagonTileFamily, HexagonTopology,
)
from .square import (
    SQUARE_BORDER_PRESET_IDS, SQUARE_ORIENTATIONS, SQUARE_PRESETS,
    SQUARE_TILE_FAMILY, SQUARE_TOPOLOGY, SquareTileFamily, SquareTopology,
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
    "SQUARE_BORDER_PRESET_IDS", "SQUARE_ORIENTATIONS", "SQUARE_PRESETS",
    "SQUARE_TILE_FAMILY", "SQUARE_TOPOLOGY", "SquareTileFamily",
    "SquareTopology",
    "TileOrientationSpec", "TileSizePreset", "TileSystemSelection",
    "TileTopology", "get_tile_family", "get_tile_family_for_geometry_shape",
    "production_tile_families", "resolve_tile_system",
]

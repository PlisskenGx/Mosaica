"""Hexagon metadata and delegation to validated geometry/adjacency."""

from dataclasses import dataclass

from ..geometry import custom_counted_hex_geometry, vertex_constrained_panel_hex_geometry
from ..model import MosaicConfig
from ..processing import tile_neighbors
from .types import MM_PER_INCH, TileOrientationSpec, TileSizePreset, TileSystemSelection

HEXAGON_ORIENTATIONS = (
    TileOrientationSpec("point_top", "Point Top"),
    TileOrientationSpec("flat_top", "Flat Top"),
)
HEXAGON_PRESETS = (
    TileSizePreset("s", 20.0, "Detailed", "More detail · More pieces", display_name="S"),
    TileSizePreset("m", 24.0, "Balanced", "Balanced detail · Balanced pieces", True, "M"),
    TileSizePreset("l", 28.0, "Bold", "Stronger mosaic · Fewer pieces", display_name="L"),
)
HEXAGON_BORDER_PRESET_IDS = ("none", "solid", "double", "alternating")


@dataclass(frozen=True)
class HexagonTopology:
    expected_neighbor_degree: int = 6

    def neighbors(self, row, column, rows, columns, orientation_id):
        if orientation_id not in {value.id for value in HEXAGON_ORIENTATIONS}:
            raise ValueError(f"Unsupported Hexagon orientation: {orientation_id}")
        config = MosaicConfig(
            tile_shape="hex",
            hex_orientation="pointy" if orientation_id == "point_top" else "flat",
        )
        return tuple(tile_neighbors(row, column, rows, columns, config))


HEXAGON_TOPOLOGY = HexagonTopology()


@dataclass(frozen=True)
class HexagonTileFamily:
    id: str = "hexagon"
    display_name: str = "Hexagon"
    topology: HexagonTopology = HEXAGON_TOPOLOGY

    def orientations(self): return HEXAGON_ORIENTATIONS
    def presets(self): return HEXAGON_PRESETS

    def normalize_orientation(self, orientation_id):
        if orientation_id not in {value.id for value in HEXAGON_ORIENTATIONS}:
            raise ValueError(f"Unsupported Hexagon orientation: {orientation_id}")
        return orientation_id

    def preset(self, preset_id):
        try:
            return {value.id: value for value in HEXAGON_PRESETS}[preset_id]
        except KeyError as exc:
            raise ValueError(f"Unknown Hexagon tile preset: {preset_id}") from exc

    def selection(self, orientation_id, preset_id):
        orientation = self.normalize_orientation(orientation_id)
        self.preset(preset_id)
        return TileSystemSelection(self.id, orientation, preset_id)

    def _config(self, preset_id, orientation_id, grout_mm, width_in=None, height_in=None):
        preset = self.preset(preset_id)
        orientation = self.normalize_orientation(orientation_id)
        return MosaicConfig(
            tile_shape="hex", tile_width_in=preset.flat_to_flat_in,
            tile_height_in=preset.flat_to_flat_in,
            grout_width_in=grout_mm / MM_PER_INCH,
            hex_orientation=orientation, target_width_in=width_in,
            target_height_in=height_in,
        )

    def build_preset_panel(self, preset_id, orientation_id, grout_mm, width_in, height_in):
        config = self._config(preset_id, orientation_id, grout_mm, width_in, height_in)
        return vertex_constrained_panel_hex_geometry(config, width_in, height_in)

    def build_custom_grid(self, preset_id, orientation_id, grout_mm, tiles_across, tiles_down):
        return custom_counted_hex_geometry(
            self._config(preset_id, orientation_id, grout_mm), tiles_across, tiles_down,
        )

    def neighbors(self, row, column, rows, columns, orientation_id):
        return list(self.topology.neighbors(
            row, column, rows, columns,
            self.normalize_orientation(orientation_id),
        ))

    def supported_border_presets(self):
        return HEXAGON_BORDER_PRESET_IDS

    def artwork_includes_clipped(self): return True
    def protects_clipped_without_border(self): return False


HEXAGON_TILE_FAMILY = HexagonTileFamily()

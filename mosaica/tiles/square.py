"""Square Designer tile-family metadata and deterministic 2D delegation."""

from dataclasses import dataclass

from ..geometry import counted_square_geometry, panel_square_geometry
from .types import TileOrientationSpec, TileSizePreset, TileSystemSelection


SQUARE_ORIENTATIONS = (TileOrientationSpec("straight", "Straight"),)
SQUARE_PRESETS = (
    TileSizePreset("s", 16.0, "Detailed", "More detail · More pieces", display_name="S", dimension_kind="side_length"),
    TileSizePreset("m", 20.0, "Balanced", "Balanced detail · Balanced pieces", True, "M", "side_length"),
    TileSizePreset("l", 24.0, "Bold", "Stronger mosaic · Fewer pieces", display_name="L", dimension_kind="side_length"),
)
SQUARE_BORDER_PRESET_IDS = ("none", "solid")


@dataclass(frozen=True)
class SquareTopology:
    expected_neighbor_degree: int = 4

    def neighbors(self, row, column, rows, columns, orientation_id):
        if orientation_id != "straight":
            raise ValueError(f"Unsupported Square orientation: {orientation_id}")
        return tuple(
            (candidate_row, candidate_column)
            for candidate_row, candidate_column in (
                (row, column - 1), (row, column + 1),
                (row - 1, column), (row + 1, column),
            )
            if 0 <= candidate_row < rows and 0 <= candidate_column < columns
        )


SQUARE_TOPOLOGY = SquareTopology()


@dataclass(frozen=True)
class SquareTileFamily:
    id: str = "square"
    display_name: str = "Square"
    topology: SquareTopology = SQUARE_TOPOLOGY

    def orientations(self): return SQUARE_ORIENTATIONS
    def presets(self): return SQUARE_PRESETS

    def normalize_orientation(self, orientation_id):
        if orientation_id != "straight":
            raise ValueError(f"Unsupported Square orientation: {orientation_id}")
        return orientation_id

    def preset(self, preset_id):
        try:
            return {value.id: value for value in SQUARE_PRESETS}[preset_id]
        except KeyError as exc:
            raise ValueError(f"Unknown Square tile preset: {preset_id}") from exc

    def selection(self, orientation_id, preset_id):
        self.normalize_orientation(orientation_id)
        self.preset(preset_id)
        return TileSystemSelection(self.id, "straight", preset_id)

    def build_preset_panel(self, preset_id, orientation_id, grout_mm, width_in, height_in):
        preset = self.preset(preset_id)
        self.normalize_orientation(orientation_id)
        return panel_square_geometry(
            preset.primary_dimension_mm / 25.4, grout_mm / 25.4,
            width_in, height_in,
        )

    def build_custom_grid(self, preset_id, orientation_id, grout_mm, tiles_across, tiles_down):
        preset = self.preset(preset_id)
        self.normalize_orientation(orientation_id)
        return counted_square_geometry(
            preset.primary_dimension_mm / 25.4, grout_mm / 25.4,
            tiles_across, tiles_down,
        )

    def neighbors(self, row, column, rows, columns, orientation_id):
        return list(self.topology.neighbors(
            row, column, rows, columns, self.normalize_orientation(orientation_id),
        ))

    def supported_border_presets(self): return SQUARE_BORDER_PRESET_IDS
    def artwork_includes_clipped(self): return True
    def protects_clipped_without_border(self): return False


SQUARE_TILE_FAMILY = SquareTileFamily()

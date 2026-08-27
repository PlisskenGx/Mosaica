"""Small shared value types for production tile-family routing."""

from dataclasses import dataclass
from math import sqrt
from typing import Protocol

MM_PER_INCH = 25.4


@dataclass(frozen=True)
class TileOrientationSpec:
    id: str
    display_name: str


@dataclass(frozen=True)
class TileSizePreset:
    id: str
    primary_dimension_mm: float
    title: str
    summary: str
    recommended: bool = False
    display_name: str | None = None
    dimension_kind: str = "flat_to_flat"

    @property
    def flat_to_flat_mm(self): return self.primary_dimension_mm
    @property
    def flat_to_flat_in(self): return self.primary_dimension_mm / MM_PER_INCH
    @property
    def side_length_mm(self): return self.primary_dimension_mm / sqrt(3.0)

    def to_dict(self):
        return {
            "id": self.id, "flat_to_flat_mm": self.flat_to_flat_mm,
            "flat_to_flat_in": self.flat_to_flat_in,
            "side_length_mm": self.side_length_mm, "title": self.title,
            "summary": self.summary, "recommended": self.recommended,
        }


@dataclass(frozen=True)
class TileSystemSelection:
    family_id: str
    orientation_id: str
    preset_id: str


class TileFamily(Protocol):
    id: str
    display_name: str
    def orientations(self): ...
    def normalize_orientation(self, orientation_id): ...
    def presets(self): ...
    def preset(self, preset_id): ...
    def selection(self, orientation_id, preset_id): ...
    def build_preset_panel(self, preset_id, orientation_id, grout_mm, width_in, height_in): ...
    def build_custom_grid(self, preset_id, orientation_id, grout_mm, tiles_across, tiles_down): ...
    def neighbors(self, row, column, rows, columns, orientation_id): ...

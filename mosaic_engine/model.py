from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


RGB = tuple[int, int, int]


@dataclass(frozen=True)
class PaletteColor:
    name: str
    rgb: RGB
    sku: str | None = None


@dataclass(frozen=True)
class MosaicConfig:
    # Tile geometry
    tile_shape: str = "square"

    tile_width_in: float = 1.0
    tile_height_in: float = 1.0

    grout_width_in: float = 0.0

    hex_orientation: str = "pointy"

    # Exact finished panel dimensions
    target_width_in: float | None = None
    target_height_in: float | None = None

    # Explicit grid mode
    columns: int | None = None
    rows: int | None = None

    # Artwork layout
    fit: str = "contain"

    # Reserved inner margin.
    #
    # Eventually this can be occupied by a decorative
    # border while the artwork remains inside it.
    artwork_inset_in: float = 0.0

    # Artwork transform inside its available region.
    artwork_scale: float = 1.0
    artwork_offset_x_in: float = 0.0
    artwork_offset_y_in: float = 0.0

    # Color interpretation
    quantization_mode: str = "palette"

    bw_threshold: int = 128

    coverage_threshold: float = 0.45

    invert_bw: bool = False

    cleanup_passes: int = 0

    dither: bool = False

    background_rgb: RGB = (255, 255, 255)


@dataclass
class MosaicResult:
    columns: int
    rows: int

    grid: list[list[int]]

    palette: Sequence[PaletteColor]

    source_path: Path

    physical_width_in: float
    physical_height_in: float

    config: MosaicConfig

    geometry: object

    def counts(self) -> dict[str, int]:
        counts = {
            p.name: 0
            for p in self.palette
        }

        for placement in self.geometry.placements:
            if placement.piece_type == "outside":
                continue

            idx = self.grid[
                placement.row
            ][
                placement.column
            ]

            counts[
                self.palette[idx].name
            ] += 1

        return counts

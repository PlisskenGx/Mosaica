from __future__ import annotations

import csv

from pathlib import Path

from PIL import (
    Image,
    ImageDraw,
)

from .model import MosaicResult
from .project import MosaicProject


ExportableMosaic = MosaicResult | MosaicProject


def _assignment_index(
    result: ExportableMosaic,
    row: int,
    column: int,
) -> int:
    if isinstance(result, MosaicProject):
        return result.effective_index(row, column)

    return result.grid[row][column]


def export_counts_csv(
    result: ExportableMosaic,
    path: str | Path,
) -> Path:

    path = Path(path)

    counts = result.counts()

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)

        writer.writerow(
            [
                "color",
                "sku",
                "rgb",
                "tile_count",
            ]
        )

        for color in result.palette:

            writer.writerow(
                [
                    color.name,

                    color.sku
                    or "",

                    "#%02X%02X%02X"
                    % color.rgb,

                    counts[
                        color.name
                    ],
                ]
            )

    return path


def export_grid_csv(
    result: ExportableMosaic,
    path: str | Path,
) -> Path:

    path = Path(path)

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)

        writer.writerow(
            [
                "row/col"
            ]
            + list(
                range(
                    1,
                    result.columns + 1,
                )
            )
        )

        for row in range(result.rows):

            writer.writerow(
                [row + 1]
                + [
                    result.palette[
                        _assignment_index(
                            result,
                            row,
                            column,
                        )
                    ].name
                    for column in range(result.columns)
                ]
            )

    return path


def export_placements_csv(
    result: ExportableMosaic,
    path: str | Path,
) -> Path:

    path = Path(path)

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)

        writer.writerow(
            [
                "row",
                "column",
                "piece_type",
                "piece_fraction",
                "center_x_in",
                "center_y_in",
                "visible_x_in",
                "visible_y_in",
                "color",
                "sku",
            ]
        )

        for placement in (
            result.geometry.placements
        ):

            if (
                placement.piece_type
                == "outside"
            ):
                continue

            color = result.palette[
                _assignment_index(
                    result,
                    placement.row,
                    placement.column,
                )
            ]

            visible_x, visible_y = (
                placement.visible_centroid_in
            )

            writer.writerow(
                [
                    placement.row + 1,
                    placement.column + 1,

                    placement.piece_type,

                    (
                        f"{placement.piece_fraction:.6f}"
                    ),

                    (
                        f"{placement.center_x_in:.6f}"
                    ),

                    (
                        f"{placement.center_y_in:.6f}"
                    ),

                    (
                        f"{visible_x:.6f}"
                    ),

                    (
                        f"{visible_y:.6f}"
                    ),

                    color.name,

                    color.sku or "",
                ]
            )

    return path


def export_preview_png(
    result: ExportableMosaic,
    path: str | Path,
    pixels_per_inch: int = 48,
    draw_grid: bool = True,
) -> Path:

    """
    Render the actual physical polygons calculated by
    the geometry engine.
    """

    if pixels_per_inch <= 0:
        raise ValueError(
            "Preview pixels per inch must be positive."
        )

    path = Path(path)

    pad = 4

    width_px = (
        max(
            1,
            round(
                result.physical_width_in
                * pixels_per_inch
            ),
        )
        + pad * 2
    )

    height_px = (
        max(
            1,
            round(
                result.physical_height_in
                * pixels_per_inch
            ),
        )
        + pad * 2
    )

    img = Image.new(
        "RGB",
        (
            width_px,
            height_px,
        ),
        "white",
    )

    draw = ImageDraw.Draw(img)

    for placement in (
        result.geometry.placements
    ):

        color_index = (
            _assignment_index(
                result,
                placement.row,
                placement.column,
            )
        )

        vertices = [
            (
                round(
                    x
                    * pixels_per_inch
                )
                + pad,

                round(
                    y
                    * pixels_per_inch
                )
                + pad,
            )

            for x, y
            in placement.vertices_in
        ]

        if draw_grid:

            outline = (
                128,
                128,
                128,
            )

        else:

            outline = None

        draw.polygon(
            vertices,

            fill=(
                result.palette[
                    color_index
                ].rgb
            ),

            outline=outline,
        )

    img.save(path)

    return path

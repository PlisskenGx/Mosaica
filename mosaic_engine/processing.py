from __future__ import annotations

from collections import Counter

from .model import (
    MosaicConfig,
    PaletteColor,
    RGB,
)


def luminance(
    rgb: RGB,
) -> float:
    """
    Calculate perceived luminance from an RGB color.

    Returns approximately 0-255.
    """

    r, g, b = rgb

    return (
        0.2126 * r
        + 0.7152 * g
        + 0.0722 * b
    )


def palette_extremes(
    palette: list[PaletteColor],
) -> tuple[int, int]:
    """
    Return:

        darkest palette index
        lightest palette index

    Black/white mode uses these as foreground and
    background colors.

    They do not literally need to be pure black and
    pure white.
    """

    if len(palette) < 2:
        raise ValueError(
            "Black/white mode requires at least "
            "two palette colors."
        )

    darkest = min(
        range(len(palette)),
        key=lambda i: luminance(
            palette[i].rgb
        ),
    )

    lightest = max(
        range(len(palette)),
        key=lambda i: luminance(
            palette[i].rgb
        ),
    )

    if darkest == lightest:
        raise ValueError(
            "Black/white mode requires two "
            "visually distinct palette colors."
        )

    return darkest, lightest


def threshold_grid(
    sampled: list[list[RGB]],
    palette: list[PaletteColor],
    threshold: int = 128,
    invert: bool = False,
) -> list[list[int]]:
    """
    Convert sampled RGB artwork into a two-color
    mosaic using a luminance threshold.

    Anything darker than the threshold becomes the
    darkest palette color.

    Anything equal to or brighter than the threshold
    becomes the lightest palette color.
    """

    if not 0 <= threshold <= 255:
        raise ValueError(
            "Threshold must be between 0 and 255."
        )

    dark_index, light_index = (
        palette_extremes(palette)
    )

    result: list[list[int]] = []

    for row in sampled:
        output_row: list[int] = []

        for rgb in row:
            is_dark = (
                luminance(rgb)
                < threshold
            )

            if invert:
                is_dark = not is_dark

            output_row.append(
                dark_index
                if is_dark
                else light_index
            )

        result.append(output_row)

    return result


def _square_neighbors(
    row: int,
    col: int,
    rows: int,
    cols: int,
) -> list[tuple[int, int]]:
    """
    Four edge-sharing neighbors for square tiles.
    """

    candidates = [
        (row - 1, col),
        (row + 1, col),
        (row, col - 1),
        (row, col + 1),
    ]

    return [
        (r, c)
        for r, c in candidates
        if (
            0 <= r < rows
            and 0 <= c < cols
        )
    ]


def _pointy_hex_neighbors(
    row: int,
    col: int,
    rows: int,
    cols: int,
) -> list[tuple[int, int]]:
    """
    Six neighbors for the odd-row-offset pointy hex
    lattice used by geometry.py.

    Odd-numbered rows are shifted right.
    """

    if row % 2 == 0:
        offsets = [
            (0, -1),
            (0, 1),

            (-1, -1),
            (-1, 0),

            (1, -1),
            (1, 0),
        ]

    else:
        offsets = [
            (0, -1),
            (0, 1),

            (-1, 0),
            (-1, 1),

            (1, 0),
            (1, 1),
        ]

    result: list[
        tuple[int, int]
    ] = []

    for dr, dc in offsets:
        r = row + dr
        c = col + dc

        if (
            0 <= r < rows
            and 0 <= c < cols
        ):
            result.append(
                (r, c)
            )

    return result


def _flat_hex_neighbors(
    row: int,
    col: int,
    rows: int,
    cols: int,
) -> list[tuple[int, int]]:
    """
    Six neighbors for the odd-column-offset flat hex
    lattice used by geometry.py.

    Odd-numbered columns are shifted down.
    """

    if col % 2 == 0:
        offsets = [
            (-1, 0),
            (1, 0),

            (-1, -1),
            (0, -1),

            (-1, 1),
            (0, 1),
        ]

    else:
        offsets = [
            (-1, 0),
            (1, 0),

            (0, -1),
            (1, -1),

            (0, 1),
            (1, 1),
        ]

    result: list[
        tuple[int, int]
    ] = []

    for dr, dc in offsets:
        r = row + dr
        c = col + dc

        if (
            0 <= r < rows
            and 0 <= c < cols
        ):
            result.append(
                (r, c)
            )

    return result


def tile_neighbors(
    row: int,
    col: int,
    rows: int,
    cols: int,
    config: MosaicConfig,
) -> list[tuple[int, int]]:
    """
    Return the edge-sharing physical neighbors for a
    tile according to the active tile geometry.
    """

    if config.tile_shape == "square":
        return _square_neighbors(
            row,
            col,
            rows,
            cols,
        )

    if config.tile_shape == "hex":

        if (
            config.hex_orientation
            == "pointy"
        ):
            return _pointy_hex_neighbors(
                row,
                col,
                rows,
                cols,
            )

        if (
            config.hex_orientation
            == "flat"
        ):
            return _flat_hex_neighbors(
                row,
                col,
                rows,
                cols,
            )

    raise ValueError(
        "Unsupported tile geometry for cleanup: "
        f"{config.tile_shape} / "
        f"{config.hex_orientation}"
    )


def cleanup_grid(
    grid: list[list[int]],
    config: MosaicConfig,
    passes: int = 1,
) -> list[list[int]]:
    """
    Remove isolated tile noise using physical tile
    adjacency.

    A tile is changed only when:

    1. It has at most one same-color neighbor.
    2. Another color holds a strict majority among
       its physical neighbors.

    This is intentionally conservative so legitimate
    edges and narrow features are less likely to be
    destroyed.
    """

    if passes <= 0:
        return [
            row[:]
            for row in grid
        ]

    if not grid:
        return []

    rows = len(grid)
    cols = len(grid[0])

    if cols == 0:
        return [
            row[:]
            for row in grid
        ]

    if any(
        len(row) != cols
        for row in grid
    ):
        raise ValueError(
            "Cleanup requires a rectangular grid."
        )

    current = [
        row[:]
        for row in grid
    ]

    for _ in range(passes):
        updated = [
            row[:]
            for row in current
        ]

        for row in range(rows):
            for col in range(cols):

                current_color = (
                    current[row][col]
                )

                neighbors = tile_neighbors(
                    row,
                    col,
                    rows,
                    cols,
                    config,
                )

                if not neighbors:
                    continue

                neighbor_colors = [
                    current[r][c]
                    for r, c
                    in neighbors
                ]

                same_support = sum(
                    1
                    for color
                    in neighbor_colors
                    if color
                    == current_color
                )

                # Don't touch a tile that already
                # has meaningful local support.
                if same_support > 1:
                    continue

                counts = Counter(
                    neighbor_colors
                )

                replacement_color, support = (
                    counts.most_common(1)[0]
                )

                # Strict majority of the available
                # physical neighbors.
                required = (
                    len(neighbor_colors)
                    // 2
                    + 1
                )

                if (
                    replacement_color
                    != current_color
                    and support >= required
                ):
                    updated[row][col] = (
                        replacement_color
                    )

        current = updated

    return current
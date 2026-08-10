from __future__ import annotations

from .model import (
    RGB,
    PaletteColor,
)


def _srgb_to_linear(
    c: int,
) -> float:

    x = c / 255.0

    return (
        x / 12.92
        if x <= 0.04045
        else (
            (x + 0.055)
            / 1.055
        ) ** 2.4
    )


def rgb_distance(
    a: RGB,
    b: RGB,
) -> float:

    """
    Weighted RGB distance in linear-light color space.
    """

    ar, ag, ab = map(
        _srgb_to_linear,
        a,
    )

    br, bg, bb = map(
        _srgb_to_linear,
        b,
    )

    dr = ar - br
    dg = ag - bg
    db = ab - bb

    return (
        0.2126 * dr * dr
        + 0.7152 * dg * dg
        + 0.0722 * db * db
    )


def nearest_palette_index(
    rgb: RGB,
    palette: list[PaletteColor],
) -> int:

    return min(
        range(
            len(palette)
        ),
        key=lambda i: rgb_distance(
            rgb,
            palette[i].rgb,
        ),
    )
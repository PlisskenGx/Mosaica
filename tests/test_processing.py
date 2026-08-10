from mosaic_engine.model import (
    MosaicConfig,
    PaletteColor,
)

from mosaic_engine.processing import (
    cleanup_grid,
    threshold_grid,
)


PALETTE = [
    PaletteColor(
        name="Black",
        rgb=(0, 0, 0),
    ),

    PaletteColor(
        name="White",
        rgb=(255, 255, 255),
    ),
]


def test_bw_threshold():

    sampled = [
        [
            (20, 20, 20),
            (240, 240, 240),
        ]
    ]

    result = threshold_grid(
        sampled=sampled,
        palette=PALETTE,
        threshold=128,
    )

    assert result == [
        [
            0,
            1,
        ]
    ]


def test_bw_threshold_invert():

    sampled = [
        [
            (20, 20, 20),
            (240, 240, 240),
        ]
    ]

    result = threshold_grid(
        sampled=sampled,
        palette=PALETTE,
        threshold=128,
        invert=True,
    )

    assert result == [
        [
            1,
            0,
        ]
    ]


def test_hex_cleanup_removes_isolated_tile():

    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy",
    )

    grid = [
        [0, 0, 0],
        [0, 1, 0],
        [0, 0, 0],
    ]

    result = cleanup_grid(
        grid=grid,
        config=config,
        passes=1,
    )

    assert result[
        1
    ][
        1
    ] == 0
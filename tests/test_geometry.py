from math import isclose

from mosaica.geometry import (
    build_geometry,
)

from mosaica.model import (
    MosaicConfig,
)


def test_single_pointy_hex_across_flats():

    config = MosaicConfig(
        tile_shape="hex",
        tile_width_in=1.0,
        grout_width_in=0.0,
        hex_orientation="pointy",
    )

    geometry = build_geometry(
        config,
        1,
        1,
    )

    assert isclose(
        geometry.width_in,
        1.0,
        abs_tol=1e-9,
    )

    assert isclose(
        geometry.height_in,
        2 / (3 ** 0.5),
        abs_tol=1e-9,
    )


def test_pointy_hex_horizontal_pitch_includes_grout():

    config = MosaicConfig(
        tile_shape="hex",
        tile_width_in=1.0,
        grout_width_in=1 / 16,
        hex_orientation="pointy",
    )

    geometry = build_geometry(
        config,
        2,
        1,
    )

    p0 = geometry.placement(
        0,
        0,
    )

    p1 = geometry.placement(
        0,
        1,
    )

    assert isclose(
        p1.center_x_in
        - p0.center_x_in,

        1.0 + 1 / 16,

        abs_tol=1e-9,
    )


def test_pointy_rows_are_staggered():

    config = MosaicConfig(
        tile_shape="hex",
        tile_width_in=1.0,
        grout_width_in=0.0,
        hex_orientation="pointy",
    )

    geometry = build_geometry(
        config,
        2,
        2,
    )

    assert isclose(
        geometry
        .placement(
            1,
            0,
        )
        .center_x_in
        -
        geometry
        .placement(
            0,
            0,
        )
        .center_x_in,

        0.5,

        abs_tol=1e-9,
    )
from math import isclose

from mosaic_engine.boundary import (
    Rect,
    clip_polygon_to_rect,
    polygon_area,
)

from mosaic_engine.geometry import (
    build_panel_geometry,
)

from mosaic_engine.model import (
    MosaicConfig,
)


def test_polygon_rectangle_clip():

    polygon = (
        (-1.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (-1.0, 1.0),
    )

    clipped = clip_polygon_to_rect(
        polygon,

        Rect(
            0.0,
            0.0,
            1.0,
            1.0,
        ),
    )

    assert isclose(
        polygon_area(clipped),
        1.0,
        abs_tol=1e-9,
    )


def test_exact_hex_panel_size():

    config = MosaicConfig(
        tile_shape="hex",
        tile_width_in=1.0,
        grout_width_in=1 / 16,
        hex_orientation="pointy",
    )

    geometry = build_panel_geometry(
        config,
        46.0,
        20.0,
    )

    assert isclose(
        geometry.width_in,
        46.0,
        abs_tol=1e-9,
    )

    assert isclose(
        geometry.height_in,
        20.0,
        abs_tol=1e-9,
    )


def test_panel_contains_cut_tiles():

    config = MosaicConfig(
        tile_shape="hex",
        tile_width_in=1.0,
        grout_width_in=1 / 16,
        hex_orientation="pointy",
    )

    geometry = build_panel_geometry(
        config,
        10.0,
        6.0,
    )

    cut_pieces = [
        placement
        for placement
        in geometry.placements
        if placement.piece_type
        in {
            "half",
            "edge_cut",
        }
    ]

    assert len(cut_pieces) > 0


def test_panel_contains_half_tiles():

    config = MosaicConfig(
        tile_shape="hex",
        tile_width_in=1.0,
        grout_width_in=0.0,
        hex_orientation="pointy",
    )

    geometry = build_panel_geometry(
        config,
        10.0,
        6.0,
    )

    halves = [
        placement
        for placement
        in geometry.placements
        if placement.piece_type
        == "half"
    ]

    assert len(halves) > 0
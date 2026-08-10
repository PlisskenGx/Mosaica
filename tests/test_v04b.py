from pathlib import Path

from PIL import Image

from mosaic_engine.engine import (
    _classify_coverage,
    generate_mosaic,
)
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import MosaicConfig, PaletteColor


PALETTE = [
    PaletteColor("Black", (0, 0, 0)),
    PaletteColor("White", (255, 255, 255)),
]


def _save_image(tmp_path, image):
    path = tmp_path / "source.png"
    image.save(path)
    return path


def _square_result(tmp_path, **config_values):
    source = _save_image(
        tmp_path,
        Image.new("RGB", (20, 20), "black"),
    )
    config = MosaicConfig(
        columns=5,
        rows=5,
        fit="stretch",
        **config_values,
    )
    return generate_mosaic(source, PALETTE, config)


def test_clipped_perimeter_pieces_use_background(tmp_path):
    source = _save_image(
        tmp_path,
        Image.new("RGB", (20, 20), "black"),
    )
    result = generate_mosaic(
        source,
        PALETTE,
        MosaicConfig(
            tile_shape="hex",
            target_width_in=5,
            target_height_in=4,
            fit="stretch",
            cleanup_passes=2,
        ),
    )

    clipped = [
        placement
        for placement in result.geometry.placements
        if placement.piece_type not in {"full", "outside"}
    ]

    assert clipped
    assert all(
        result.grid[p.row][p.column] == 1
        for p in clipped
    )
    assert any(
        result.grid[p.row][p.column] == 0
        for p in result.geometry.placements
        if p.piece_type == "full"
    )


def test_artwork_inset_reserves_background_region(tmp_path):
    result = _square_result(
        tmp_path,
        artwork_inset_in=1.0,
    )

    assert result.grid[0] == [1, 1, 1, 1, 1]
    assert result.grid[2][2] == 0
    assert [row[0] for row in result.grid] == [1, 1, 1, 1, 1]


def test_artwork_scale_changes_mapping_not_geometry(tmp_path):
    full = _square_result(tmp_path)
    scaled = _square_result(
        tmp_path,
        artwork_scale=0.4,
    )

    assert scaled.geometry == full.geometry
    assert scaled.grid[2][2] == 0
    assert scaled.grid[0][0] == 1


def test_artwork_x_offset_moves_artwork_horizontally(tmp_path):
    centered = _square_result(
        tmp_path,
        artwork_scale=0.4,
    )
    shifted = _square_result(
        tmp_path,
        artwork_scale=0.4,
        artwork_offset_x_in=1.0,
    )

    centered_columns = {
        column
        for row in centered.grid
        for column, value in enumerate(row)
        if value == 0
    }
    shifted_columns = {
        column
        for row in shifted.grid
        for column, value in enumerate(row)
        if value == 0
    }

    assert min(shifted_columns) > min(centered_columns)


def test_artwork_y_offset_moves_artwork_vertically(tmp_path):
    centered = _square_result(
        tmp_path,
        artwork_scale=0.4,
    )
    shifted = _square_result(
        tmp_path,
        artwork_scale=0.4,
        artwork_offset_y_in=1.0,
    )

    centered_rows = {
        row
        for row, values in enumerate(centered.grid)
        if 0 in values
    }
    shifted_rows = {
        row
        for row, values in enumerate(shifted.grid)
        if 0 in values
    }

    assert min(shifted_rows) > min(centered_rows)


def test_bw_uses_tile_area_coverage(tmp_path):
    image = Image.new("RGB", (100, 100), "white")

    for x in range(50):
        for y in range(100):
            image.putpixel((x, y), (0, 0, 0))

    result = generate_mosaic(
        _save_image(tmp_path, image),
        PALETTE,
        MosaicConfig(
            columns=1,
            rows=1,
            fit="stretch",
            quantization_mode="bw",
            coverage_threshold=0.45,
        ),
    )

    assert result.grid == [[0]]


def test_borderline_coverage_favors_supported_stroke():
    config = MosaicConfig(
        tile_shape="hex",
        quantization_mode="bw",
        coverage_threshold=0.45,
    )
    geometry = build_geometry(config, 3, 3)
    coverage = [
        [0.0, 0.60, 0.0],
        [0.0, 0.44, 0.0],
        [0.0, 0.60, 0.0],
    ]

    result = _classify_coverage(
        coverage,
        geometry,
        PALETTE,
        config,
    )

    assert result[1][1] == 0


def test_borderline_coverage_suppresses_isolated_protrusion():
    config = MosaicConfig(
        tile_shape="hex",
        quantization_mode="bw",
        coverage_threshold=0.45,
    )
    geometry = build_geometry(config, 3, 3)
    coverage = [
        [0.0, 0.0, 0.0],
        [0.0, 0.46, 0.0],
        [0.0, 0.0, 0.0],
    ]

    result = _classify_coverage(
        coverage,
        geometry,
        PALETTE,
        config,
    )

    assert result[1][1] == 1


def test_palette_centroid_sampling_regression(tmp_path):
    image = Image.new("RGB", (2, 1), "white")
    image.putpixel((0, 0), (0, 0, 0))
    result = generate_mosaic(
        _save_image(tmp_path, image),
        PALETTE,
        MosaicConfig(
            columns=2,
            rows=1,
            fit="stretch",
        ),
    )

    assert result.grid == [[0, 1]]

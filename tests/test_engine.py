import builtins
from pathlib import Path

import pytest
from PIL import Image

from mosaic_engine.engine import (
    _open_source_image,
    generate_mosaic,
)
from mosaic_engine.export import export_preview_png
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import (
    MosaicConfig,
    MosaicResult,
    PaletteColor,
)


PALETTE = [
    PaletteColor(
        name="Black",
        rgb=(0, 0, 0),
    ),
]


def _block_cairosvg_import(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "cairosvg":
            raise OSError("native Cairo is unavailable")

        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        builtins,
        "__import__",
        blocked_import,
    )


def test_raster_loading_does_not_require_cairo(
    tmp_path,
    monkeypatch,
):
    _block_cairosvg_import(monkeypatch)

    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2), "black").save(source)

    with _open_source_image(
        source,
        (255, 255, 255),
    ) as image:
        assert image.size == (2, 2)


def test_svg_loading_reports_missing_cairo(
    monkeypatch,
):
    _block_cairosvg_import(monkeypatch)

    with pytest.raises(
        RuntimeError,
        match="SVG input requires CairoSVG and the native Cairo library",
    ):
        _open_source_image(
            Path("source.svg"),
            (255, 255, 255),
        )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (MosaicConfig(tile_width_in=0), "Tile width"),
        (MosaicConfig(tile_height_in=0), "Tile height"),
        (MosaicConfig(grout_width_in=-0.1), "Grout width"),
        (MosaicConfig(target_width_in=0), "Target width"),
        (MosaicConfig(target_height_in=-1), "Target height"),
        (MosaicConfig(artwork_scale=0), "Artwork scale"),
        (MosaicConfig(coverage_threshold=0), "Coverage threshold"),
        (MosaicConfig(coverage_threshold=1.1), "Coverage threshold"),
    ],
)
def test_invalid_config_is_rejected_before_source_loading(
    config,
    message,
):
    with pytest.raises(ValueError, match=message):
        generate_mosaic(
            "missing.png",
            PALETTE,
            config,
        )


def test_exact_square_panel_reports_unsupported_clipping():
    with pytest.raises(
        ValueError,
        match="supported for hex tiles only",
    ):
        generate_mosaic(
            "missing.png",
            PALETTE,
            MosaicConfig(target_width_in=10),
        )


def test_preview_ppi_must_be_positive(tmp_path):
    config = MosaicConfig(columns=1, rows=1)
    geometry = build_geometry(config, 1, 1)
    result = MosaicResult(
        columns=1,
        rows=1,
        grid=[[0]],
        palette=PALETTE,
        source_path=Path("source.png"),
        physical_width_in=1.0,
        physical_height_in=1.0,
        config=config,
        geometry=geometry,
    )

    with pytest.raises(
        ValueError,
        match="pixels per inch must be positive",
    ):
        export_preview_png(
            result,
            tmp_path / "preview.png",
            pixels_per_inch=0,
        )

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from .color import nearest_palette_index
from .geometry import GridGeometry, build_geometry, build_panel_geometry
from .model import MosaicConfig, MosaicResult, PaletteColor
from .processing import cleanup_grid, threshold_grid


def _open_source_image(
    source_path: Path,
    background_rgb: tuple[int, int, int],
) -> Image.Image:
    """
    Open raster artwork directly.

    SVG artwork is rasterized in memory at a high
    working resolution.

    Transparent areas are composited onto the mosaic
    background color before sampling.
    """

    if source_path.suffix.lower() == ".svg":
        try:
            import cairosvg
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "SVG input requires CairoSVG and the native "
                "Cairo library. Install both, or convert the "
                "source artwork to a raster image such as PNG."
            ) from exc

        png_bytes = cairosvg.svg2png(
            url=str(source_path),
            output_width=4096,
        )

        img = Image.open(
            BytesIO(png_bytes)
        ).convert("RGBA")

    else:
        img = Image.open(
            source_path
        ).convert("RGBA")

    background = Image.new(
        "RGBA",
        img.size,
        (
            background_rgb[0],
            background_rgb[1],
            background_rgb[2],
            255,
        ),
    )

    composited = Image.alpha_composite(
        background,
        img,
    )

    return composited.convert("RGB")


def _aspect_from_source(
    source_size: tuple[int, int],
) -> float:

    sw, sh = source_size

    if sw <= 0 or sh <= 0:
        raise ValueError(
            "Source image has invalid dimensions."
        )

    return sw / sh


def _candidate_geometry(
    config: MosaicConfig,
    cols: int,
    rows: int,
) -> GridGeometry:

    return build_geometry(
        config,
        max(1, cols),
        max(1, rows),
    )


def _largest_grid_within(
    config: MosaicConfig,
    target_w: float | None,
    target_h: float | None,
    source_aspect: float,
) -> tuple[int, int]:

    tile = max(
        config.tile_width_in,
        1e-9,
    )

    approx_cols = max(
        1,
        int(
            (
                target_w
                or (
                    target_h
                    or tile
                )
                * source_aspect
            )
            / tile
        ),
    )

    approx_rows = max(
        1,
        int(
            (
                target_h
                or (
                    target_w
                    or tile
                )
                / source_aspect
            )
            / tile
        ),
    )

    max_cols = max(
        8,
        approx_cols * 2 + 8,
    )

    max_rows = max(
        8,
        approx_rows * 2 + 8,
    )

    best: tuple[
        float,
        int,
        int,
    ] | None = None

    for rows in range(
        1,
        max_rows + 1,
    ):

        for cols in range(
            1,
            max_cols + 1,
        ):

            geom = _candidate_geometry(
                config,
                cols,
                rows,
            )

            if (
                target_w is not None
                and geom.width_in
                > target_w + 1e-9
            ):
                continue

            if (
                target_h is not None
                and geom.height_in
                > target_h + 1e-9
            ):
                continue

            aspect = (
                geom.width_in
                / geom.height_in
            )

            aspect_error = (
                abs(
                    aspect
                    - source_aspect
                )
                / source_aspect
            )

            utilization = 0.0

            if target_w is not None:
                utilization += (
                    geom.width_in
                    / target_w
                )

            if target_h is not None:
                utilization += (
                    geom.height_in
                    / target_h
                )

            score = (
                aspect_error * 10.0
                - utilization
            )

            if (
                best is None
                or score < best[0]
            ):
                best = (
                    score,
                    cols,
                    rows,
                )

    if best is None:
        return 1, 1

    return (
        best[1],
        best[2],
    )


def _resolve_grid(
    config: MosaicConfig,
    source_size: tuple[int, int],
) -> tuple[int, int]:

    if (
        config.columns
        and config.rows
    ):
        return (
            config.columns,
            config.rows,
        )

    source_aspect = (
        _aspect_from_source(
            source_size
        )
    )

    if config.columns:

        cols = config.columns

        candidates = []

        for r in range(
            1,
            max(
                4,
                cols * 4,
            ) + 1,
        ):

            geometry = (
                _candidate_geometry(
                    config,
                    cols,
                    r,
                )
            )

            candidates.append(
                (
                    abs(
                        (
                            geometry.width_in
                            /
                            geometry.height_in
                        )
                        - source_aspect
                    ),
                    r,
                )
            )

        return (
            cols,
            min(candidates)[1],
        )

    if config.rows:

        rows = config.rows

        candidates = []

        for c in range(
            1,
            max(
                4,
                rows * 4,
            ) + 1,
        ):

            geometry = (
                _candidate_geometry(
                    config,
                    c,
                    rows,
                )
            )

            candidates.append(
                (
                    abs(
                        (
                            geometry.width_in
                            /
                            geometry.height_in
                        )
                        - source_aspect
                    ),
                    c,
                )
            )

        return (
            min(candidates)[1],
            rows,
        )

    if (
        config.target_width_in
        or config.target_height_in
    ):

        return _largest_grid_within(
            config=config,

            target_w=(
                config.target_width_in
            ),

            target_h=(
                config.target_height_in
            ),

            source_aspect=source_aspect,
        )

    raise ValueError(
        "Specify columns/rows or target "
        "physical width/height."
    )


def _sample_source(
    img: Image.Image,
    geometry: GridGeometry,
    fit: str,
    background: tuple[int, int, int],
) -> list[
    list[
        tuple[int, int, int]
    ]
]:

    """
        Sample artwork at the visible centroid of each
        physical tile or clipped edge piece.
    """

    img = img.convert("RGB")

    sw, sh = img.size

    gw = geometry.width_in
    gh = geometry.height_in

    if gw <= 0 or gh <= 0:
        raise ValueError(
            "Geometry has invalid "
            "physical dimensions."
        )

    source_aspect = sw / sh
    target_aspect = gw / gh

    grid = [
        [
            background
            for _ in range(
                geometry.columns
            )
        ]
        for _ in range(
            geometry.rows
        )
    ]

    px = img.load()

    if fit == "contain":

        if (
            source_aspect
            >= target_aspect
        ):

            rendered_w = gw

            rendered_h = (
                gw
                / source_aspect
            )

        else:

            rendered_h = gh

            rendered_w = (
                gh
                * source_aspect
            )

        left = (
            gw
            - rendered_w
        ) / 2.0

        top = (
            gh
            - rendered_h
        ) / 2.0

        for placement in (
            geometry.placements
        ):

            if placement.piece_type == "outside":
                continue

            x, y = (
                placement.visible_centroid_in
            )

            if (
                x < left
                or x > left + rendered_w
                or y < top
                or y > top + rendered_h
            ):
                continue

            u = (
                x - left
            ) / rendered_w

            v = (
                y - top
            ) / rendered_h

            sx = min(
                sw - 1,
                max(
                    0,
                    round(
                        u
                        * (sw - 1)
                    ),
                ),
            )

            sy = min(
                sh - 1,
                max(
                    0,
                    round(
                        v
                        * (sh - 1)
                    ),
                ),
            )

            grid[
                placement.row
            ][
                placement.column
            ] = px[sx, sy]

        return grid

    if fit == "stretch":

        for placement in (
            geometry.placements
        ):

            if placement.piece_type == "outside":
                continue

            x, y = (
                placement.visible_centroid_in
            )

            u = (
                x / gw
            )

            v = (
                y / gh
            )

            sx = min(
                sw - 1,
                max(
                    0,
                    round(
                        u
                        * (sw - 1)
                    ),
                ),
            )

            sy = min(
                sh - 1,
                max(
                    0,
                    round(
                        v
                        * (sh - 1)
                    ),
                ),
            )

            grid[
                placement.row
            ][
                placement.column
            ] = px[sx, sy]

        return grid

    if fit == "cover":

        scale = max(
            gw / sw,
            gh / sh,
        )

        rendered_w = (
            sw * scale
        )

        rendered_h = (
            sh * scale
        )

        left = (
            gw
            - rendered_w
        ) / 2.0

        top = (
            gh
            - rendered_h
        ) / 2.0

        for placement in (
            geometry.placements
        ):

            if placement.piece_type == "outside":
                continue

            x, y = (
                placement.visible_centroid_in
            )

            u = (
                x - left
            ) / rendered_w

            v = (
                y - top
            ) / rendered_h

            sx = min(
                sw - 1,
                max(
                    0,
                    round(
                        u
                        * (sw - 1)
                    ),
                ),
            )

            sy = min(
                sh - 1,
                max(
                    0,
                    round(
                        v
                        * (sh - 1)
                    ),
                ),
            )

            grid[
                placement.row
            ][
                placement.column
            ] = px[sx, sy]

        return grid

    raise ValueError(
        f"Unsupported fit mode: {fit}"
    )


def _quantize(
    sampled: list[
        list[
            tuple[int, int, int]
        ]
    ],
    palette: list[PaletteColor],
    config: MosaicConfig,
) -> list[list[int]]:
    """
    Convert sampled artwork colors into actual
    palette indices.
    """

    if (
        config.quantization_mode
        == "bw"
    ):

        return threshold_grid(
            sampled=sampled,

            palette=palette,

            threshold=(
                config.bw_threshold
            ),

            invert=(
                config.invert_bw
            ),
        )

    if (
        config.quantization_mode
        == "palette"
    ):

        return [
            [
                nearest_palette_index(
                    rgb,
                    palette,
                )
                for rgb in row
            ]
            for row in sampled
        ]

    raise ValueError(
        "Unsupported quantization mode: "
        f"{config.quantization_mode}"
    )


def _resolve_panel_size(
    config: MosaicConfig,
    source_size: tuple[int, int],
) -> tuple[float, float] | None:
    """
    Resolve an exact rectangular finished panel.

    If only one dimension is supplied, preserve the
    source artwork aspect ratio.
    """

    width = config.target_width_in
    height = config.target_height_in

    if (
        width is None
        and height is None
    ):
        return None

    source_width, source_height = (
        source_size
    )

    aspect = (
        source_width
        / source_height
    )

    if (
        width is not None
        and height is None
    ):
        height = (
            width / aspect
        )

    elif (
        height is not None
        and width is None
    ):
        width = (
            height * aspect
        )

    assert width is not None
    assert height is not None

    return (
        width,
        height,
    )


def generate_mosaic(
    source: str | Path,
    palette: list[PaletteColor],
    config: MosaicConfig,
) -> MosaicResult:

    if config.tile_width_in <= 0:
        raise ValueError(
            "Tile width must be positive."
        )

    if config.tile_height_in <= 0:
        raise ValueError(
            "Tile height must be positive."
        )

    if config.grout_width_in < 0:
        raise ValueError(
            "Grout width cannot be negative."
        )

    if (
        config.target_width_in is not None
        and config.target_width_in <= 0
    ):
        raise ValueError(
            "Target width must be positive."
        )

    if (
        config.target_height_in is not None
        and config.target_height_in <= 0
    ):
        raise ValueError(
            "Target height must be positive."
        )

    if config.artwork_scale <= 0:
        raise ValueError(
            "Artwork scale must be positive."
        )

    if (
        config.tile_shape == "square"
        and (
            config.target_width_in is not None
            or config.target_height_in is not None
        )
    ):
        raise ValueError(
            "Exact-panel clipping is currently "
            "supported for hex tiles only."
        )

    if not palette:
        raise ValueError(
            "Palette cannot be empty."
        )

    if (
        config.cleanup_passes
        < 0
    ):
        raise ValueError(
            "Cleanup passes cannot be negative."
        )

    source_path = Path(source)

    with _open_source_image(
        source_path,
        config.background_rgb,
    ) as img:

        panel_size = _resolve_panel_size(
            config,
            img.size,
        )

        if panel_size is not None:

            panel_width, panel_height = (
                panel_size
            )

            geometry = build_panel_geometry(
                config,
                panel_width,
                panel_height,
            )

            cols = geometry.columns
            rows = geometry.rows

        else:

            cols, rows = _resolve_grid(
                config,
                img.size,
            )

            geometry = build_geometry(
                config,
                cols,
                rows,
            )

        sampled = _sample_source(
            img=img,

            geometry=geometry,

            fit=config.fit,

            background=(
                config.background_rgb
            ),
        )

        grid = _quantize(
            sampled,
            palette,
            config,
        )

        if (
            config.cleanup_passes
            > 0
        ):

            grid = cleanup_grid(
                grid=grid,

                config=config,

                passes=(
                    config.cleanup_passes
                ),
            )

    return MosaicResult(
        columns=cols,
        rows=rows,

        grid=grid,

        palette=palette,

        source_path=source_path,

        physical_width_in=(
            geometry.width_in
        ),

        physical_height_in=(
            geometry.height_in
        ),

        config=config,

        geometry=geometry,
    )

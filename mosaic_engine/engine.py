from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from .color import nearest_palette_index
from .geometry import GridGeometry, build_geometry, build_panel_geometry
from .model import MosaicConfig, MosaicResult, PaletteColor
from .processing import (
    cleanup_grid,
    luminance,
    palette_extremes,
    threshold_grid,
    tile_neighbors,
)


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


@dataclass(frozen=True)
class _ArtworkLayout:
    bounds: object
    left: float
    top: float
    width: float
    height: float
    source_width: int
    source_height: int

    def source_pixel(
        self,
        x: float,
        y: float,
    ) -> tuple[int, int] | None:
        if (
            x < self.bounds.left
            or x > self.bounds.right
            or y < self.bounds.top
            or y > self.bounds.bottom
            or x < self.left
            or x > self.left + self.width
            or y < self.top
            or y > self.top + self.height
        ):
            return None

        u = (x - self.left) / self.width
        v = (y - self.top) / self.height

        return (
            min(
                self.source_width - 1,
                max(0, round(u * (self.source_width - 1))),
            ),
            min(
                self.source_height - 1,
                max(0, round(v * (self.source_height - 1))),
            ),
        )


def _artwork_layout(
    img: Image.Image,
    geometry: GridGeometry,
    config: MosaicConfig,
) -> _ArtworkLayout:
    sw, sh = img.size
    bounds = geometry.artwork_bounds
    available_w = bounds.width
    available_h = bounds.height
    source_aspect = sw / sh
    target_aspect = available_w / available_h

    if config.fit == "stretch":
        rendered_w = available_w
        rendered_h = available_h
    elif config.fit == "contain":
        if source_aspect >= target_aspect:
            rendered_w = available_w
            rendered_h = available_w / source_aspect
        else:
            rendered_h = available_h
            rendered_w = available_h * source_aspect
    elif config.fit == "cover":
        if source_aspect >= target_aspect:
            rendered_h = available_h
            rendered_w = available_h * source_aspect
        else:
            rendered_w = available_w
            rendered_h = available_w / source_aspect
    else:
        raise ValueError(
            f"Unsupported fit mode: {config.fit}"
        )

    rendered_w *= config.artwork_scale
    rendered_h *= config.artwork_scale

    center_x = (
        (bounds.left + bounds.right) / 2.0
        + config.artwork_offset_x_in
    )
    center_y = (
        (bounds.top + bounds.bottom) / 2.0
        + config.artwork_offset_y_in
    )

    return _ArtworkLayout(
        bounds=bounds,
        left=center_x - rendered_w / 2.0,
        top=center_y - rendered_h / 2.0,
        width=rendered_w,
        height=rendered_h,
        source_width=sw,
        source_height=sh,
    )


def _sample_source(
    img: Image.Image,
    geometry: GridGeometry,
    config: MosaicConfig,
) -> list[list[tuple[int, int, int]]]:
    """Sample full tiles at their centroids for palette mode."""

    img = img.convert("RGB")
    layout = _artwork_layout(img, geometry, config)
    px = img.load()
    grid = [
        [config.background_rgb for _ in range(geometry.columns)]
        for _ in range(geometry.rows)
    ]

    for placement in geometry.placements:
        if placement.piece_type != "full":
            continue

        source_pixel = layout.source_pixel(
            placement.center_x_in,
            placement.center_y_in,
        )

        if source_pixel is not None:
            grid[placement.row][placement.column] = px[source_pixel]

    return grid


def _point_in_polygon(
    x: float,
    y: float,
    polygon,
) -> bool:
    inside = False
    previous_x, previous_y = polygon[-1]

    for current_x, current_y in polygon:
        if (
            (current_y > y) != (previous_y > y)
            and x < (
                (previous_x - current_x)
                * (y - current_y)
                / (previous_y - current_y)
                + current_x
            )
        ):
            inside = not inside

        previous_x, previous_y = current_x, current_y

    return inside


def _coverage_grid(
    img: Image.Image,
    geometry: GridGeometry,
    config: MosaicConfig,
    samples_per_axis: int = 24,
) -> list[list[float]]:
    """Estimate foreground area within each full physical tile."""

    img = img.convert("RGB")
    layout = _artwork_layout(img, geometry, config)
    px = img.load()
    coverage = [
        [0.0 for _ in range(geometry.columns)]
        for _ in range(geometry.rows)
    ]

    for placement in geometry.placements:
        if placement.piece_type != "full":
            continue

        polygon = placement.vertices_in
        min_x = min(x for x, _ in polygon)
        max_x = max(x for x, _ in polygon)
        min_y = min(y for _, y in polygon)
        max_y = max(y for _, y in polygon)
        inside_count = 0
        foreground_count = 0

        for sample_y in range(samples_per_axis):
            y = min_y + (
                (sample_y + 0.5)
                / samples_per_axis
                * (max_y - min_y)
            )

            for sample_x in range(samples_per_axis):
                x = min_x + (
                    (sample_x + 0.5)
                    / samples_per_axis
                    * (max_x - min_x)
                )

                if not _point_in_polygon(x, y, polygon):
                    continue

                inside_count += 1
                source_pixel = layout.source_pixel(x, y)

                if source_pixel is None:
                    continue

                is_foreground = (
                    luminance(px[source_pixel])
                    < config.bw_threshold
                )

                if config.invert_bw:
                    is_foreground = not is_foreground

                if is_foreground:
                    foreground_count += 1

        if inside_count:
            coverage[placement.row][placement.column] = (
                foreground_count / inside_count
            )

    return coverage


def _classify_coverage(
    coverage: list[list[float]],
    geometry: GridGeometry,
    palette: list[PaletteColor],
    config: MosaicConfig,
) -> list[list[int]]:
    dark_index, light_index = palette_extremes(palette)
    foreground_index = light_index if config.invert_bw else dark_index
    background_index = dark_index if config.invert_bw else light_index
    threshold = config.coverage_threshold
    borderline_width = 0.08
    preliminary = [
        [value >= threshold for value in row]
        for row in coverage
    ]
    active = {
        (placement.row, placement.column)
        for placement in geometry.placements
        if placement.piece_type == "full"
    }
    result = [
        [background_index for _ in range(geometry.columns)]
        for _ in range(geometry.rows)
    ]

    for row, column in active:
        value = coverage[row][column]
        foreground = preliminary[row][column]

        if abs(value - threshold) <= borderline_width:
            neighbors = [
                neighbor
                for neighbor in tile_neighbors(
                    row,
                    column,
                    geometry.rows,
                    geometry.columns,
                    config,
                )
                if neighbor in active
            ]
            support = sum(
                preliminary[r][c]
                for r, c in neighbors
            )

            if (
                foreground
                and neighbors
                and support == 0
            ):
                foreground = False
            elif not foreground and support >= 2:
                foreground = True

        if foreground:
            result[row][column] = foreground_index

    return result


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

    if not 0 < config.coverage_threshold <= 1:
        raise ValueError(
            "Coverage threshold must be greater than 0 "
            "and at most 1."
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

        if config.quantization_mode == "bw":
            coverage = _coverage_grid(
                img=img,
                geometry=geometry,
                config=config,
            )
            grid = _classify_coverage(
                coverage=coverage,
                geometry=geometry,
                palette=palette,
                config=config,
            )
        else:
            sampled = _sample_source(
                img=img,
                geometry=geometry,
                config=config,
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

                geometry=geometry,
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

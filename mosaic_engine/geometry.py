from __future__ import annotations

from dataclasses import dataclass
from math import (
    cos,
    floor,
    pi,
    sin,
    sqrt,
)
from typing import Literal

from .boundary import (
    Rect,
    clip_polygon_to_rect,
    polygon_area,
    polygon_centroid,
)
from .model import MosaicConfig


Point = tuple[float, float]

TileShape = Literal[
    "square",
    "hex",
]


@dataclass(frozen=True)
class TilePlacement:
    row: int
    column: int

    center_x_in: float
    center_y_in: float

    # Original uncut tile
    full_vertices_in: tuple[
        Point,
        ...
    ]

    # Actual visible/cut piece
    vertices_in: tuple[
        Point,
        ...
    ]

    piece_type: str

    # Fraction of original tile area remaining.
    piece_fraction: float

    @property
    def visible_centroid_in(
        self,
    ) -> Point:

        if not self.vertices_in:
            return (
                self.center_x_in,
                self.center_y_in,
            )

        return polygon_centroid(
            self.vertices_in
        )


@dataclass(frozen=True)
class GridGeometry:
    shape: TileShape

    columns: int
    rows: int

    width_in: float
    height_in: float

    placements: tuple[
        TilePlacement,
        ...
    ]

    panel_bounds: Rect

    artwork_bounds: Rect

    def placement(
        self,
        row: int,
        column: int,
    ) -> TilePlacement:

        return self.placements[
            row * self.columns
            + column
        ]


def _polygon(
    center_x: float,
    center_y: float,
    radius: float,
    start_deg: float,
) -> tuple[Point, ...]:

    return tuple(
        (
            center_x
            + radius
            * cos(
                (
                    start_deg
                    + i * 60.0
                )
                * pi
                / 180.0
            ),

            center_y
            + radius
            * sin(
                (
                    start_deg
                    + i * 60.0
                )
                * pi
                / 180.0
            ),
        )
        for i in range(6)
    )


def _piece_type(
    full_polygon: tuple[
        Point,
        ...
    ],
    clipped_polygon: tuple[
        Point,
        ...
    ],
) -> tuple[str, float]:

    full_area = polygon_area(
        full_polygon
    )

    clipped_area = polygon_area(
        clipped_polygon
    )

    if (
        full_area <= 1e-12
        or clipped_area <= 1e-12
    ):
        return "outside", 0.0

    fraction = (
        clipped_area
        / full_area
    )

    if fraction >= 0.999999:
        return "full", 1.0

    if abs(
        fraction - 0.5
    ) <= 0.04:
        return "half", fraction

    return "edge_cut", fraction


def _artwork_bounds(
    width: float,
    height: float,
    inset: float,
) -> Rect:

    if inset < 0:
        raise ValueError(
            "Artwork inset cannot be negative."
        )

    if (
        inset * 2 >= width
        or inset * 2 >= height
    ):
        raise ValueError(
            "Artwork inset is too large "
            "for the panel."
        )

    return Rect(
        left=inset,
        top=inset,
        right=width - inset,
        bottom=height - inset,
    )


def square_geometry(
    config: MosaicConfig,
    columns: int,
    rows: int,
) -> GridGeometry:

    if columns < 1 or rows < 1:
        raise ValueError(
            "Grid dimensions must be positive."
        )

    w = config.tile_width_in
    h = config.tile_height_in
    g = config.grout_width_in

    placements: list[
        TilePlacement
    ] = []

    for row in range(rows):
        for col in range(columns):

            x0 = col * (
                w + g
            )

            y0 = row * (
                h + g
            )

            vertices = (
                (x0, y0),
                (x0 + w, y0),
                (x0 + w, y0 + h),
                (x0, y0 + h),
            )

            placements.append(
                TilePlacement(
                    row=row,
                    column=col,

                    center_x_in=(
                        x0 + w / 2
                    ),

                    center_y_in=(
                        y0 + h / 2
                    ),

                    full_vertices_in=(
                        vertices
                    ),

                    vertices_in=vertices,

                    piece_type="full",
                    piece_fraction=1.0,
                )
            )

    width = (
        columns * w
        + max(
            0,
            columns - 1,
        ) * g
    )

    height = (
        rows * h
        + max(
            0,
            rows - 1,
        ) * g
    )

    panel = Rect(
        0.0,
        0.0,
        width,
        height,
    )

    return GridGeometry(
        shape="square",

        columns=columns,
        rows=rows,

        width_in=width,
        height_in=height,

        placements=tuple(
            placements
        ),

        panel_bounds=panel,

        artwork_bounds=(
            _artwork_bounds(
                width,
                height,
                config.artwork_inset_in,
            )
        ),
    )


def hex_geometry(
    config: MosaicConfig,
    columns: int,
    rows: int,
) -> GridGeometry:

    """
    Original full-tile grid mode.

    This remains useful when the user explicitly
    requests rows/columns rather than an exact panel.
    """

    across_flats = (
        config.tile_width_in
    )

    grout = (
        config.grout_width_in
    )

    pitch = (
        across_flats
        + grout
    )

    radius = (
        across_flats
        / sqrt(3.0)
    )

    placements: list[
        TilePlacement
    ] = []

    if (
        config.hex_orientation
        == "pointy"
    ):

        row_step = (
            sqrt(3.0)
            / 2.0
            * pitch
        )

        for row in range(rows):

            x_offset = (
                pitch / 2.0
                if row % 2
                else 0.0
            )

            for col in range(columns):

                cx = (
                    col * pitch
                    + x_offset
                )

                cy = (
                    row * row_step
                )

                polygon = _polygon(
                    cx,
                    cy,
                    radius,
                    30.0,
                )

                placements.append(
                    TilePlacement(
                        row=row,
                        column=col,

                        center_x_in=cx,
                        center_y_in=cy,

                        full_vertices_in=(
                            polygon
                        ),

                        vertices_in=polygon,

                        piece_type="full",
                        piece_fraction=1.0,
                    )
                )

    elif (
        config.hex_orientation
        == "flat"
    ):

        col_step = (
            sqrt(3.0)
            / 2.0
            * pitch
        )

        for row in range(rows):
            for col in range(columns):

                y_offset = (
                    pitch / 2.0
                    if col % 2
                    else 0.0
                )

                cx = (
                    col * col_step
                )

                cy = (
                    row * pitch
                    + y_offset
                )

                polygon = _polygon(
                    cx,
                    cy,
                    radius,
                    0.0,
                )

                placements.append(
                    TilePlacement(
                        row=row,
                        column=col,

                        center_x_in=cx,
                        center_y_in=cy,

                        full_vertices_in=(
                            polygon
                        ),

                        vertices_in=polygon,

                        piece_type="full",
                        piece_fraction=1.0,
                    )
                )

    else:
        raise ValueError(
            "Unsupported hex orientation: "
            f"{config.hex_orientation}"
        )

    xs = [
        x
        for placement
        in placements
        for x, _
        in placement.vertices_in
    ]

    ys = [
        y
        for placement
        in placements
        for _, y
        in placement.vertices_in
    ]

    min_x = min(xs)
    min_y = min(ys)

    normalized: list[
        TilePlacement
    ] = []

    for placement in placements:

        full_vertices = tuple(
            (
                x - min_x,
                y - min_y,
            )
            for x, y
            in placement.full_vertices_in
        )

        vertices = tuple(
            (
                x - min_x,
                y - min_y,
            )
            for x, y
            in placement.vertices_in
        )

        normalized.append(
            TilePlacement(
                row=placement.row,
                column=placement.column,

                center_x_in=(
                    placement.center_x_in
                    - min_x
                ),

                center_y_in=(
                    placement.center_y_in
                    - min_y
                ),

                full_vertices_in=(
                    full_vertices
                ),

                vertices_in=vertices,

                piece_type="full",
                piece_fraction=1.0,
            )
        )

    max_x = max(
        x
        for placement
        in normalized
        for x, _
        in placement.vertices_in
    )

    max_y = max(
        y
        for placement
        in normalized
        for _, y
        in placement.vertices_in
    )

    panel = Rect(
        0.0,
        0.0,
        max_x,
        max_y,
    )

    return GridGeometry(
        shape="hex",

        columns=columns,
        rows=rows,

        width_in=max_x,
        height_in=max_y,

        placements=tuple(
            normalized
        ),

        panel_bounds=panel,

        artwork_bounds=(
            _artwork_bounds(
                max_x,
                max_y,
                config.artwork_inset_in,
            )
        ),
    )


def panel_hex_geometry(
    config: MosaicConfig,
    width_in: float,
    height_in: float,
) -> GridGeometry:
    """
    Generate a hex lattice across an EXACT rectangular
    finished panel.

    Tiles crossing the boundary are geometrically
    clipped into real edge pieces.
    """

    if (
        width_in <= 0
        or height_in <= 0
    ):
        raise ValueError(
            "Panel dimensions must be positive."
        )

    across_flats = (
        config.tile_width_in
    )

    grout = (
        config.grout_width_in
    )

    pitch = (
        across_flats
        + grout
    )

    radius = (
        across_flats
        / sqrt(3.0)
    )

    panel = Rect(
        0.0,
        0.0,
        width_in,
        height_in,
    )

    placements: list[
        TilePlacement
    ] = []

    if (
        config.hex_orientation
        == "pointy"
    ):

        row_step = (
            sqrt(3.0)
            / 2.0
            * pitch
        )

        # Enough rows to cover the bottom edge.
        rows = (
            floor(
                (
                    height_in
                    + radius
                )
                / row_step
            )
            + 1
        )

        # Pointy hex has half-width equal to
        # across_flats / 2.
        half_width = (
            across_flats / 2.0
        )

        columns = (
            floor(
                (
                    width_in
                    + half_width
                )
                / pitch
            )
            + 1
        )

        lattice_min_x = -half_width
        lattice_max_x = (
            (columns - 1) * pitch
            + (
                pitch / 2.0
                if rows > 1
                else 0.0
            )
            + half_width
        )
        lattice_min_y = -radius
        lattice_max_y = (
            (rows - 1) * row_step
            + radius
        )

        shift_x = (
            width_in
            - (
                lattice_max_x
                - lattice_min_x
            )
        ) / 2.0 - lattice_min_x

        shift_y = (
            height_in
            - (
                lattice_max_y
                - lattice_min_y
            )
        ) / 2.0 - lattice_min_y

        for row in range(rows):

            x_offset = (
                pitch / 2.0
                if row % 2
                else 0.0
            )

            cy = (
                row * row_step
                + shift_y
            )

            for col in range(columns):

                cx = (
                    col * pitch
                    + x_offset
                    + shift_x
                )

                full_polygon = _polygon(
                    cx,
                    cy,
                    radius,
                    30.0,
                )

                clipped = (
                    clip_polygon_to_rect(
                        full_polygon,
                        panel,
                    )
                )

                (
                    piece_type,
                    fraction,
                ) = _piece_type(
                    full_polygon,
                    clipped,
                )

                placements.append(
                    TilePlacement(
                        row=row,
                        column=col,

                        center_x_in=cx,
                        center_y_in=cy,

                        full_vertices_in=(
                            full_polygon
                        ),

                        vertices_in=clipped,

                        piece_type=(
                            piece_type
                        ),

                        piece_fraction=(
                            fraction
                        ),
                    )
                )

    elif (
        config.hex_orientation
        == "flat"
    ):

        col_step = (
            sqrt(3.0)
            / 2.0
            * pitch
        )

        half_height = (
            across_flats / 2.0
        )

        columns = (
            floor(
                (
                    width_in
                    + radius
                )
                / col_step
            )
            + 1
        )

        rows = (
            floor(
                (
                    height_in
                    + half_height
                )
                / pitch
            )
            + 1
        )

        lattice_min_x = -radius
        lattice_max_x = (
            (columns - 1) * col_step
            + radius
        )
        lattice_min_y = -half_height
        lattice_max_y = (
            (rows - 1) * pitch
            + (
                pitch / 2.0
                if columns > 1
                else 0.0
            )
            + half_height
        )

        shift_x = (
            width_in
            - (
                lattice_max_x
                - lattice_min_x
            )
        ) / 2.0 - lattice_min_x

        shift_y = (
            height_in
            - (
                lattice_max_y
                - lattice_min_y
            )
        ) / 2.0 - lattice_min_y

        for row in range(rows):
            for col in range(columns):

                y_offset = (
                    pitch / 2.0
                    if col % 2
                    else 0.0
                )

                cx = (
                    col * col_step
                    + shift_x
                )

                cy = (
                    row * pitch
                    + y_offset
                    + shift_y
                )

                full_polygon = _polygon(
                    cx,
                    cy,
                    radius,
                    0.0,
                )

                clipped = (
                    clip_polygon_to_rect(
                        full_polygon,
                        panel,
                    )
                )

                (
                    piece_type,
                    fraction,
                ) = _piece_type(
                    full_polygon,
                    clipped,
                )

                placements.append(
                    TilePlacement(
                        row=row,
                        column=col,

                        center_x_in=cx,
                        center_y_in=cy,

                        full_vertices_in=(
                            full_polygon
                        ),

                        vertices_in=clipped,

                        piece_type=(
                            piece_type
                        ),

                        piece_fraction=(
                            fraction
                        ),
                    )
                )

    else:
        raise ValueError(
            "Unsupported hex orientation: "
            f"{config.hex_orientation}"
        )

    return GridGeometry(
        shape="hex",

        columns=columns,
        rows=rows,

        width_in=width_in,
        height_in=height_in,

        placements=tuple(
            placements
        ),

        panel_bounds=panel,

        artwork_bounds=(
            _artwork_bounds(
                width_in,
                height_in,
                config.artwork_inset_in,
            )
        ),
    )


def build_geometry(
    config: MosaicConfig,
    columns: int,
    rows: int,
) -> GridGeometry:

    if config.tile_shape == "square":
        return square_geometry(
            config,
            columns,
            rows,
        )

    if config.tile_shape == "hex":
        return hex_geometry(
            config,
            columns,
            rows,
        )

    raise ValueError(
        "Unsupported tile shape: "
        f"{config.tile_shape}"
    )


def build_panel_geometry(
    config: MosaicConfig,
    width_in: float,
    height_in: float,
) -> GridGeometry:

    if config.tile_shape == "hex":
        return panel_hex_geometry(
            config,
            width_in,
            height_in,
        )

    raise NotImplementedError(
        "Exact panel clipping is currently "
        "implemented for hex tiles first."
    )

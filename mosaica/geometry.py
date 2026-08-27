from __future__ import annotations

from dataclasses import dataclass, replace
from math import (
    ceil,
    cos,
    floor,
    hypot,
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
    principal_grid: bool = False
    principal_row: int | None = None
    principal_column: int | None = None

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

    orientation: str | None = None

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
        orientation=None,

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


def counted_square_geometry(
    side_in: float, grout_in: float, columns: int, rows: int,
) -> GridGeometry:
    """Build an explicit whole-Square counted grid with Straight orientation."""
    config = MosaicConfig(
        tile_shape="square", tile_width_in=side_in, tile_height_in=side_in,
        grout_width_in=grout_in,
    )
    return replace(square_geometry(config, columns, rows), orientation="straight")


def panel_square_geometry(
    side_in: float, grout_in: float, width_in: float, height_in: float,
) -> GridGeometry:
    """Center a Square lattice and clip it to an authoritative canvas rectangle.

    The smallest row/column field whose tile-and-grout span covers each canvas
    axis is centered on that axis. Any symmetric overhang becomes clipped edge
    geometry, while a pitch-compatible canvas naturally produces whole tiles.
    """
    if side_in <= 0 or width_in <= 0 or height_in <= 0:
        raise ValueError("Square tile and canvas dimensions must be positive.")
    if grout_in < 0:
        raise ValueError("Square grout cannot be negative.")
    pitch = side_in + grout_in
    columns = max(1, int(ceil((width_in + grout_in) / pitch)))
    rows = max(1, int(ceil((height_in + grout_in) / pitch)))
    field_width = columns * side_in + (columns - 1) * grout_in
    field_height = rows * side_in + (rows - 1) * grout_in
    origin_x = (width_in - field_width) / 2.0
    origin_y = (height_in - field_height) / 2.0
    panel = Rect(0.0, 0.0, width_in, height_in)
    placements = []
    for row in range(rows):
        for column in range(columns):
            left = origin_x + column * pitch
            top = origin_y + row * pitch
            full = (
                (left, top), (left + side_in, top),
                (left + side_in, top + side_in), (left, top + side_in),
            )
            clipped = tuple(clip_polygon_to_rect(full, panel))
            piece_type, fraction = _piece_type(full, clipped)
            if piece_type not in {"full", "outside"}:
                piece_type = "edge_cut"
            placements.append(TilePlacement(
                row=row, column=column,
                center_x_in=left + side_in / 2.0,
                center_y_in=top + side_in / 2.0,
                full_vertices_in=full, vertices_in=clipped,
                piece_type=piece_type, piece_fraction=fraction,
            ))
    return GridGeometry(
        shape="square", orientation="straight", columns=columns, rows=rows,
        width_in=width_in, height_in=height_in,
        placements=tuple(placements), panel_bounds=panel,
        artwork_bounds=_artwork_bounds(width_in, height_in, 0.0),
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
        orientation=(
            "point_top" if config.hex_orientation in {"pointy", "point_top"}
            else "flat_top"
        ),

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
        orientation=(
            "point_top" if config.hex_orientation in {"pointy", "point_top"}
            else "flat_top"
        ),

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


def vertex_constrained_panel_hex_geometry(
    config: MosaicConfig,
    target_width_in: float,
    target_height_in: float,
) -> GridGeometry:
    """Nearest rectangular hex panel whose cuts terminate at hex vertices.

    The two boundary phases are deliberately assigned to opposite lattice
    parities. This keeps corner tiles from being cut by two perpendicular
    boundaries and yields a finite, repeatable perimeter-piece catalog.
    """
    if target_width_in <= 0 or target_height_in <= 0:
        raise ValueError("Panel dimensions must be positive.")
    orientation = config.hex_orientation
    if orientation not in {"point_top", "flat_top"}:
        raise ValueError(f"Unsupported canonical hex orientation: {orientation}")
    across_flats = config.tile_width_in
    grout = config.grout_width_in
    pitch = across_flats + grout
    radius = across_flats / sqrt(3.0)
    stagger_step = sqrt(3.0) / 2.0 * pitch

    def nearest_count(
        target: float, step: float, *, offset: float = 0.0, even: bool = False,
    ) -> int:
        raw = max(1, round((target - offset) / step))
        candidates = range(max(1, raw - 3), raw + 4)
        valid = [value for value in candidates if not even or value % 2 == 0]
        return min(
            valid,
            key=lambda value: (abs(value * step + offset - target), value),
        )

    if orientation == "point_top":
        horizontal_intervals = nearest_count(target_width_in, pitch)
        vertical_intervals = nearest_count(
            target_height_in, stagger_step, offset=-radius, even=True,
        )
        width = horizontal_intervals * pitch
        height = vertical_intervals * stagger_step - radius
        columns = horizontal_intervals + 1
        rows = vertical_intervals + 1
        world_left = pitch / 2.0
        world_top = radius / 2.0

        def center(row: int, column: int) -> Point:
            return (
                column * pitch + (pitch / 2.0 if row % 2 else 0.0) - world_left,
                row * stagger_step - world_top,
            )

        start_deg = 30.0
    else:
        horizontal_intervals = nearest_count(
            target_width_in, stagger_step, offset=-radius, even=True,
        )
        vertical_intervals = nearest_count(target_height_in, pitch)
        width = horizontal_intervals * stagger_step - radius
        height = vertical_intervals * pitch
        columns = horizontal_intervals + 1
        rows = vertical_intervals + 1
        world_left = radius / 2.0
        world_top = pitch / 2.0

        def center(row: int, column: int) -> Point:
            return (
                column * stagger_step - world_left,
                row * pitch + (pitch / 2.0 if column % 2 else 0.0) - world_top,
            )

        start_deg = 0.0

    panel = Rect(0.0, 0.0, width, height)
    placements = []

    def clean_polygon(points: tuple[Point, ...]) -> tuple[Point, ...]:
        cleaned = []
        for point in points:
            normalized = (
                0.0 if abs(point[0]) <= 1e-10 else width if abs(point[0] - width) <= 1e-10 else point[0],
                0.0 if abs(point[1]) <= 1e-10 else height if abs(point[1] - height) <= 1e-10 else point[1],
            )
            if not cleaned or hypot(
                normalized[0] - cleaned[-1][0],
                normalized[1] - cleaned[-1][1],
            ) > 1e-10:
                cleaned.append(normalized)
        if len(cleaned) > 1 and hypot(
            cleaned[0][0] - cleaned[-1][0],
            cleaned[0][1] - cleaned[-1][1],
        ) <= 1e-10:
            cleaned.pop()
        return tuple(cleaned)

    for row in range(rows):
        for column in range(columns):
            cx, cy = center(row, column)
            full = _polygon(cx, cy, radius, start_deg)
            clipped = clean_polygon(clip_polygon_to_rect(full, panel))
            piece_type, fraction = _piece_type(full, clipped)
            if piece_type not in {"full", "outside"}:
                for point in clipped:
                    if not any(
                        abs(point[0] - vertex[0]) <= 1e-8
                        and abs(point[1] - vertex[1]) <= 1e-8
                        for vertex in full
                    ):
                        raise RuntimeError(
                            "Vertex-constrained panel produced a non-vertex cut."
                        )
            placements.append(TilePlacement(
                row=row,
                column=column,
                center_x_in=cx,
                center_y_in=cy,
                full_vertices_in=full,
                vertices_in=clipped,
                piece_type=piece_type,
                piece_fraction=fraction,
            ))
    return GridGeometry(
        shape="hex",
        orientation=orientation,
        columns=columns,
        rows=rows,
        width_in=width,
        height_in=height,
        placements=tuple(placements),
        panel_bounds=panel,
        artwork_bounds=_artwork_bounds(width, height, config.artwork_inset_in),
    )


def custom_counted_hex_geometry(
    config: MosaicConfig, tiles_across: int, tiles_down: int,
) -> GridGeometry:
    """Build an orientation-aware counted full-tile grid plus safe edge pieces."""
    orientation = config.hex_orientation
    if orientation not in {"point_top", "flat_top"}:
        raise ValueError(f"Unsupported canonical hex orientation: {orientation}")
    across_flats = config.tile_width_in
    pitch = across_flats + config.grout_width_in
    radius = across_flats / sqrt(3.0)
    stagger = sqrt(3.0) / 2.0 * pitch
    start_deg = 30.0 if orientation == "point_top" else 0.0

    def raw_center(row: int, column: int) -> Point:
        if orientation == "point_top":
            return (column * pitch + (pitch / 2.0 if row % 2 else 0.0), row * stagger)
        return (column * stagger, row * pitch + (pitch / 2.0 if column % 2 else 0.0))

    principal_keys = {
        (row, column)
        for row in range(tiles_down) for column in range(tiles_across)
    }
    candidate_keys = {
        (row, column)
        for row in range(-2, tiles_down + 2)
        for column in range(-2, tiles_across + 2)
    }
    raw_polygons = {
        key: _polygon(*raw_center(*key), radius, start_deg)
        for key in candidate_keys
    }
    principal_points = [
        point for key in principal_keys for point in raw_polygons[key]
    ]
    principal_bounds = Rect(
        min(point[0] for point in principal_points),
        min(point[1] for point in principal_points),
        max(point[0] for point in principal_points),
        max(point[1] for point in principal_points),
    )
    x_vertices = sorted({point[0] for polygon in raw_polygons.values() for point in polygon})
    y_vertices = sorted({point[1] for polygon in raw_polygons.values() for point in polygon})
    lefts = [value for value in x_vertices if principal_bounds.left - pitch <= value <= principal_bounds.left]
    rights = [value for value in x_vertices if principal_bounds.right <= value <= principal_bounds.right + pitch]
    tops = [value for value in y_vertices if principal_bounds.top - pitch <= value <= principal_bounds.top]
    bottoms = [value for value in y_vertices if principal_bounds.bottom <= value <= principal_bounds.bottom + pitch]

    best = None
    even_stagger_count = (
        tiles_down % 2 == 0
        if orientation == "point_top"
        else tiles_across % 2 == 0
    )
    if even_stagger_count:
        # The centered 23/100-pitch phase is the first stable mid-side phase
        # above the 1/6 manufacturing floor.  It keeps the requested full
        # field centered and prevents the next stagger-completion strip from
        # becoming full.
        margin = 23.0 / 100.0 * pitch
        if orientation == "point_top":
            candidates = (Rect(
                principal_bounds.left, principal_bounds.top - margin,
                principal_bounds.right, principal_bounds.bottom + margin,
            ),)
        else:
            candidates = (Rect(
                principal_bounds.left - margin, principal_bounds.top,
                principal_bounds.right + margin, principal_bounds.bottom,
            ),)
    else:
        candidates = (
            Rect(left, top, right, bottom)
            for left in lefts for right in rights
            for top in tops for bottom in bottoms
        )

    for panel in candidates:
                    left, top = panel.left, panel.top
                    right, bottom = panel.right, panel.bottom
                    supplemental = []
                    valid = True
                    for key, full in raw_polygons.items():
                        if key in principal_keys:
                            continue
                        clipped = tuple(clip_polygon_to_rect(full, panel))
                        piece_type, fraction = _piece_type(full, clipped)
                        if piece_type == "outside":
                            continue
                        if piece_type == "full":
                            supplemental.append((key, clipped, piece_type, fraction))
                            continue
                        vertex_only = all(any(
                            abs(point[0] - vertex[0]) <= 1e-8
                            and abs(point[1] - vertex[1]) <= 1e-8
                            for vertex in full
                        ) for point in clipped)
                        standardized = any(
                            abs(fraction - allowed) <= 1e-8
                            for allowed in (1 / 6, 1 / 2)
                        )
                        safe_mid_side = (
                            even_stagger_count
                            and fraction >= 1 / 6 - 1e-8
                        )
                        if not safe_mid_side and (not vertex_only or not standardized):
                            valid = False
                            break
                        supplemental.append((key, clipped, piece_type, fraction))
                    if valid:
                        full_keys = principal_keys | {
                            key for key, _, piece_type, _ in supplemental
                            if piece_type == "full"
                        }
                        visible_stagger_count = len({
                            raw_center(*key)[1 if orientation == "point_top" else 0]
                            for key in full_keys
                        })
                        requested_stagger_count = (
                            tiles_down if orientation == "point_top" else tiles_across
                        )
                        if visible_stagger_count != requested_stagger_count:
                            continue
                        score = (
                            -((right - left) * (bottom - top)),
                            len(supplemental),
                            -left, -top, -right, -bottom,
                        )
                        if best is None or score > best[0]:
                            best = (score, panel, supplemental)
    if best is None:
        raise RuntimeError("Custom grid has no vertex-constrained perimeter.")
    _, raw_panel, supplemental = best

    # Audit one additional lattice ring beyond the construction candidates.
    # Every positive-area intersection must have been considered and retained;
    # a touch at a vertex or edge has zero area and is not a physical piece.
    retained_keys = principal_keys | {key for key, *_ in supplemental}
    audit_keys = {
        (row, column)
        for row in range(-3, tiles_down + 3)
        for column in range(-3, tiles_across + 3)
    }
    for key in audit_keys:
        full = raw_polygons.get(key)
        if full is None:
            full = _polygon(*raw_center(*key), radius, start_deg)
        clipped = tuple(clip_polygon_to_rect(full, raw_panel))
        if polygon_area(clipped) > 1e-10 and key not in retained_keys:
            raise RuntimeError(
                "Custom grid omitted an intersecting supplemental lattice tile "
                f"at row {key[0]}, column {key[1]}."
            )

    if any(
        fraction < 1 / 6 - 1e-8
        for _, _, _, fraction in supplemental
    ):
        raise RuntimeError("Custom grid produced a perimeter piece smaller than 1/6 tile.")

    width = raw_panel.width
    height = raw_panel.height
    rows, columns = tiles_down + 4, tiles_across + 4
    supplemental_by_key = {key: values for key, *values in supplemental}
    placements = []
    for grid_row in range(rows):
        source_row = grid_row - 2
        for grid_column in range(columns):
            source_column = grid_column - 2
            key = (source_row, source_column)
            raw_full = raw_polygons[key]
            full = tuple(
                (point[0] - raw_panel.left, point[1] - raw_panel.top)
                for point in raw_full
            )
            raw_x, raw_y = raw_center(*key)
            if key in principal_keys:
                vertices, piece_type, fraction, principal = full, "full", 1.0, True
            elif key in supplemental_by_key:
                raw_vertices, piece_type, fraction = supplemental_by_key[key]
                vertices = tuple(
                    (point[0] - raw_panel.left, point[1] - raw_panel.top)
                    for point in raw_vertices
                )
                principal = False
            else:
                vertices, piece_type, fraction, principal = (), "outside", 0.0, False
            placements.append(TilePlacement(
                row=grid_row, column=grid_column,
                center_x_in=raw_x - raw_panel.left,
                center_y_in=raw_y - raw_panel.top,
                full_vertices_in=full, vertices_in=vertices,
                piece_type=piece_type, piece_fraction=fraction,
                principal_grid=principal,
                principal_row=source_row if principal else None,
                principal_column=source_column if principal else None,
            ))
    final_principal = {
        (value.principal_row, value.principal_column)
        for value in placements if value.principal_grid
    }
    if final_principal != principal_keys:
        raise RuntimeError("Custom grid did not preserve principal tile identity.")
    principal_rows = {
        row: sum(
            value.principal_grid and value.principal_row == row
            for value in placements
        )
        for row in range(tiles_down)
    }
    principal_columns = {
        column: sum(
            value.principal_grid and value.principal_column == column
            for value in placements
        )
        for column in range(tiles_across)
    }
    if (
        set(principal_rows) != set(range(tiles_down))
        or any(count != tiles_across for count in principal_rows.values())
    ):
        raise RuntimeError("Custom grid principal row count does not match the request.")
    if (
        set(principal_columns) != set(range(tiles_across))
        or any(count != tiles_down for count in principal_columns.values())
    ):
        raise RuntimeError("Custom grid principal column count does not match the request.")
    if any(
        value.piece_type != "full"
        or value.vertices_in != value.full_vertices_in
        for value in placements if value.principal_grid
    ):
        raise RuntimeError("Custom grid clipped a requested principal tile.")
    containment_tolerance = 1e-9
    if any(
        point[0] < -containment_tolerance
        or point[0] > width + containment_tolerance
        or point[1] < -containment_tolerance
        or point[1] > height + containment_tolerance
        for value in placements if value.principal_grid
        for point in value.full_vertices_in
    ):
        raise RuntimeError("Custom grid placed a principal tile outside the panel.")
    panel = Rect(0.0, 0.0, width, height)
    return GridGeometry(
        shape="hex", orientation=orientation,
        columns=columns, rows=rows, width_in=width, height_in=height,
        placements=tuple(placements), panel_bounds=panel,
        artwork_bounds=_artwork_bounds(width, height, config.artwork_inset_in),
    )


def vertex_constrained_panel_dimensions(
    config: MosaicConfig, target_width_in: float, target_height_in: float,
) -> tuple[float, float]:
    """Return the same nearest valid extent without allocating placements."""
    if target_width_in <= 0 or target_height_in <= 0:
        raise ValueError("Panel dimensions must be positive.")
    if config.hex_orientation not in {"point_top", "flat_top"}:
        raise ValueError(
            f"Unsupported canonical hex orientation: {config.hex_orientation}"
        )
    across_flats = config.tile_width_in
    pitch = across_flats + config.grout_width_in
    radius = across_flats / sqrt(3.0)
    stagger = sqrt(3.0) / 2.0 * pitch

    def nearest(target, step, offset=0.0, even=False):
        raw = max(1, round((target - offset) / step))
        candidates = range(max(1, raw - 3), raw + 4)
        valid = [value for value in candidates if not even or value % 2 == 0]
        return min(valid, key=lambda value: (abs(value * step + offset - target), value))

    if config.hex_orientation == "point_top":
        return (
            nearest(target_width_in, pitch) * pitch,
            nearest(target_height_in, stagger, -radius, True) * stagger - radius,
        )
    return (
        nearest(target_width_in, stagger, -radius, True) * stagger - radius,
        nearest(target_height_in, pitch) * pitch,
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

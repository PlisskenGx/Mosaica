from __future__ import annotations

from dataclasses import dataclass


Point = tuple[float, float]


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


def polygon_area(
    polygon: tuple[Point, ...],
) -> float:
    if len(polygon) < 3:
        return 0.0

    total = 0.0

    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[
            (i + 1) % len(polygon)
        ]

        total += (
            x1 * y2
            - x2 * y1
        )

    return abs(total) / 2.0


def polygon_centroid(
    polygon: tuple[Point, ...],
) -> Point:
    """
    Area-weighted centroid of a polygon.
    """

    if len(polygon) < 3:
        return 0.0, 0.0

    cross_sum = 0.0
    cx_sum = 0.0
    cy_sum = 0.0

    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[
            (i + 1) % len(polygon)
        ]

        cross = (
            x1 * y2
            - x2 * y1
        )

        cross_sum += cross

        cx_sum += (
            x1 + x2
        ) * cross

        cy_sum += (
            y1 + y2
        ) * cross

    if abs(cross_sum) < 1e-12:
        xs = [
            p[0]
            for p in polygon
        ]

        ys = [
            p[1]
            for p in polygon
        ]

        return (
            sum(xs) / len(xs),
            sum(ys) / len(ys),
        )

    return (
        cx_sum / (3.0 * cross_sum),
        cy_sum / (3.0 * cross_sum),
    )


def _clip_edge(
    polygon: list[Point],
    inside,
    intersection,
) -> list[Point]:
    if not polygon:
        return []

    output: list[Point] = []

    previous = polygon[-1]
    previous_inside = inside(
        previous
    )

    for current in polygon:
        current_inside = inside(
            current
        )

        if current_inside:

            if not previous_inside:
                output.append(
                    intersection(
                        previous,
                        current,
                    )
                )

            output.append(current)

        elif previous_inside:

            output.append(
                intersection(
                    previous,
                    current,
                )
            )

        previous = current
        previous_inside = current_inside

    return output


def clip_polygon_to_rect(
    polygon: tuple[Point, ...],
    rect: Rect,
) -> tuple[Point, ...]:
    """
    Sutherland-Hodgman clipping against a rectangle.
    """

    points = list(polygon)

    def intersect_vertical(
        p1: Point,
        p2: Point,
        x: float,
    ) -> Point:

        x1, y1 = p1
        x2, y2 = p2

        if abs(x2 - x1) < 1e-12:
            return x, y1

        t = (
            x - x1
        ) / (
            x2 - x1
        )

        return (
            x,
            y1 + t * (
                y2 - y1
            ),
        )

    def intersect_horizontal(
        p1: Point,
        p2: Point,
        y: float,
    ) -> Point:

        x1, y1 = p1
        x2, y2 = p2

        if abs(y2 - y1) < 1e-12:
            return x1, y

        t = (
            y - y1
        ) / (
            y2 - y1
        )

        return (
            x1 + t * (
                x2 - x1
            ),
            y,
        )

    # Left
    points = _clip_edge(
        points,

        lambda p: (
            p[0]
            >= rect.left
            - 1e-12
        ),

        lambda a, b: (
            intersect_vertical(
                a,
                b,
                rect.left,
            )
        ),
    )

    # Right
    points = _clip_edge(
        points,

        lambda p: (
            p[0]
            <= rect.right
            + 1e-12
        ),

        lambda a, b: (
            intersect_vertical(
                a,
                b,
                rect.right,
            )
        ),
    )

    # Top
    points = _clip_edge(
        points,

        lambda p: (
            p[1]
            >= rect.top
            - 1e-12
        ),

        lambda a, b: (
            intersect_horizontal(
                a,
                b,
                rect.top,
            )
        ),
    )

    # Bottom
    points = _clip_edge(
        points,

        lambda p: (
            p[1]
            <= rect.bottom
            + 1e-12
        ),

        lambda a, b: (
            intersect_horizontal(
                a,
                b,
                rect.bottom,
            )
        ),
    )

    return tuple(points)
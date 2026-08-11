from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, pi

from .geometry import GridGeometry
from .model import MosaicConfig
from .processing import tile_neighbors


Coordinate = tuple[int, int]
MAX_PROJECT_COLORS = 4


@dataclass(frozen=True)
class BorderPreset:
    id: str
    name: str
    depth: int
    pattern_roles: tuple[str, ...]
    preview_kind: str

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "pattern_roles": list(self.pattern_roles),
        }


BORDER_PRESETS = (
    BorderPreset("none", "None", 0, ("edge",), "none"),
    BorderPreset("solid", "Solid", 1, ("border_primary",), "solid"),
    BorderPreset(
        "double", "Double", 2,
        ("border_primary", "border_secondary"), "double",
    ),
    BorderPreset(
        "alternating", "Alternating", 1,
        ("border_primary", "border_secondary"), "alternating",
    ),
)

_PRESETS = {value.id: value for value in BORDER_PRESETS}

# These are shared Designer project roles, not a separate border palette.
# Preview colors are temporary UI mappings until the Colors milestone.
PROJECT_COLOR_ROLES = {
    "background": {"preview_hex": "#FAF9F6"},
    "edge": {"preview_hex": "#D8D6CF"},
    "border_primary": {"preview_hex": "#34373D"},
    "border_secondary": {"preview_hex": "#A87655"},
}

if len(PROJECT_COLOR_ROLES) > MAX_PROJECT_COLORS:
    raise RuntimeError("Designer project color roles exceed the four-color limit.")


@dataclass(frozen=True)
class BorderAssignment:
    tile_id: str
    row: int
    column: int
    layer: int
    color_role: str
    piece_type: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BorderLayerState:
    preset_id: str
    assignments: tuple[BorderAssignment, ...]
    border_owned_placement_ids: tuple[str, ...]
    protected_placement_ids: tuple[str, ...]
    available_artwork_placement_ids: tuple[str, ...]
    perimeter_order: tuple[str, ...]
    layer_placement_ids: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict:
        return {
            "preset_id": self.preset_id,
            "assignments": [value.to_dict() for value in self.assignments],
            "border_owned_placement_ids": list(self.border_owned_placement_ids),
            "protected_placement_ids": list(self.protected_placement_ids),
            "available_artwork_placement_ids": list(
                self.available_artwork_placement_ids
            ),
            "perimeter_order": list(self.perimeter_order),
            "layer_placement_ids": [list(value) for value in self.layer_placement_ids],
            "counts": {
                "border_owned": len(self.border_owned_placement_ids),
                "protected": len(self.protected_placement_ids),
                "available_artwork": len(self.available_artwork_placement_ids),
            },
        }


def border_preset(preset_id: str) -> BorderPreset:
    try:
        return _PRESETS[preset_id]
    except KeyError as exc:
        raise ValueError(f"Unknown border preset: {preset_id}") from exc


def _tile_id(geometry: GridGeometry, coordinate: Coordinate) -> str:
    row, column = coordinate
    return f"placement-{row * geometry.columns + column:06d}"


def _visible_coordinates(geometry: GridGeometry) -> set[Coordinate]:
    return {
        (value.row, value.column)
        for value in geometry.placements
        if value.piece_type != "outside"
    }


def _physical_neighbors(
    geometry: GridGeometry,
    coordinate: Coordinate,
) -> tuple[Coordinate, ...]:
    config = MosaicConfig(
        tile_shape=geometry.shape,
        hex_orientation="pointy",
    )
    return tuple(tile_neighbors(
        coordinate[0], coordinate[1],
        geometry.rows, geometry.columns, config,
    ))


def physical_perimeter_rings(
    geometry: GridGeometry,
    depth: int,
) -> tuple[tuple[Coordinate, ...], ...]:
    """Peel coherent physical adjacency rings from visible placements."""

    if depth < 0:
        raise ValueError("Border depth cannot be negative.")
    clipped = {
        (value.row, value.column)
        for value in geometry.placements
        if value.piece_type not in {"full", "outside"}
    }
    # Clipped pieces are a structural perimeter in every state. Preset depth
    # counts coherent rings of ordinary full placements immediately inside it.
    remaining = _visible_coordinates(geometry) - clipped
    rings = []
    for layer in range(depth):
        if not remaining:
            break
        boundary = {
            coordinate
            for coordinate in remaining
            if (
                len(_physical_neighbors(geometry, coordinate)) < 6
                or any(
                    neighbor not in remaining
                    for neighbor in _physical_neighbors(geometry, coordinate)
                )
            )
        }
        if not boundary:
            break
        ordered = perimeter_order(geometry, boundary)
        rings.append(ordered)
        remaining.difference_update(boundary)
    return tuple(rings)


def perimeter_order(
    geometry: GridGeometry,
    coordinates: set[Coordinate] | tuple[Coordinate, ...],
) -> tuple[Coordinate, ...]:
    """Clockwise physical-centroid order, starting at the top of the panel."""

    center_x = geometry.width_in / 2.0
    center_y = geometry.height_in / 2.0

    def key(coordinate: Coordinate):
        placement = geometry.placement(*coordinate)
        x, y = placement.visible_centroid_in
        angle = (atan2(y - center_y, x - center_x) + pi / 2.0) % (2.0 * pi)
        radius_squared = (x - center_x) ** 2 + (y - center_y) ** 2
        return angle, -radius_squared, coordinate

    return tuple(sorted(coordinates, key=key))


def build_border_layer(
    geometry: GridGeometry,
    preset_id: str = "none",
) -> BorderLayerState:
    preset = border_preset(preset_id)
    visible = _visible_coordinates(geometry)
    clipped = {
        (value.row, value.column)
        for value in geometry.placements
        if value.piece_type not in {"full", "outside"}
    }
    rings = physical_perimeter_rings(geometry, preset.depth)
    clipped_order = perimeter_order(geometry, clipped)

    if preset.id == "none":
        owned_by_layer = (clipped_order,)
    else:
        first_full_ring = rings[0] if rings else ()
        outer = perimeter_order(geometry, set((*clipped_order, *first_full_ring)))
        owned_by_layer = (outer, *rings[1:preset.depth])

    outer_order = owned_by_layer[0]

    assignments = []
    outer_index = {coordinate: index for index, coordinate in enumerate(outer_order)}
    for layer, coordinates in enumerate(owned_by_layer):
        for coordinate in coordinates:
            placement = geometry.placement(*coordinate)
            if preset.id == "none":
                role = "edge"
            elif preset.id == "alternating":
                role = preset.pattern_roles[outer_index[coordinate] % 2]
            elif preset.id == "double":
                role = preset.pattern_roles[min(layer, len(preset.pattern_roles) - 1)]
            else:
                role = preset.pattern_roles[0]
            assignments.append(BorderAssignment(
                tile_id=_tile_id(geometry, coordinate),
                row=coordinate[0],
                column=coordinate[1],
                layer=layer,
                color_role=role,
                piece_type=placement.piece_type,
            ))

    assignments.sort(key=lambda value: (value.layer, value.tile_id))
    owned = tuple(sorted(value.tile_id for value in assignments))
    available = tuple(sorted(
        _tile_id(geometry, coordinate)
        for coordinate in visible
        if _tile_id(geometry, coordinate) not in set(owned)
        and geometry.placement(*coordinate).piece_type == "full"
    ))
    return BorderLayerState(
        preset_id=preset.id,
        assignments=tuple(assignments),
        border_owned_placement_ids=owned,
        protected_placement_ids=owned,
        available_artwork_placement_ids=available,
        perimeter_order=tuple(_tile_id(geometry, value) for value in outer_order),
        layer_placement_ids=tuple(
            tuple(_tile_id(geometry, value) for value in coordinates)
            for coordinates in owned_by_layer
        ),
    )

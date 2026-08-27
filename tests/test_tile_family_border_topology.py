from inspect import getsource

import pytest

import mosaica.border as border_module
from mosaica.border import BORDER_PRESETS, build_border_layer, perimeter_order, physical_perimeter_rings
from mosaica.designer import DesignerProjectShell
from mosaica.model import MosaicConfig
from mosaica.processing import tile_neighbors
from mosaica.tiles import get_tile_family, get_tile_family_for_geometry_shape


def _legacy_neighbors(geometry, coordinate):
    config = MosaicConfig(
        tile_shape="hex",
        hex_orientation="pointy" if geometry.orientation == "point_top" else "flat",
    )
    return tuple(tile_neighbors(
        coordinate[0], coordinate[1], geometry.rows, geometry.columns, config,
    ))


def _legacy_rings(geometry, depth):
    clipped = {
        (value.row, value.column) for value in geometry.placements
        if value.piece_type not in {"full", "outside"}
    }
    remaining = {
        (value.row, value.column) for value in geometry.placements
        if value.piece_type != "outside"
    } - clipped
    rings = []
    for _ in range(depth):
        boundary = {
            coordinate for coordinate in remaining
            if len(_legacy_neighbors(geometry, coordinate)) < 6
            or any(value not in remaining for value in _legacy_neighbors(geometry, coordinate))
        }
        if not boundary:
            break
        rings.append(perimeter_order(geometry, boundary))
        remaining.difference_update(boundary)
    return tuple(rings)


def test_hex_topology_owns_coordination_and_delegates_both_orientations():
    family = get_tile_family("hexagon")
    assert family.topology.expected_neighbor_degree == 6
    for orientation, legacy in (("point_top", "pointy"), ("flat_top", "flat")):
        config = MosaicConfig(tile_shape="hex", hex_orientation=legacy)
        assert family.topology.neighbors(2, 3, 7, 8, orientation) == tuple(
            tile_neighbors(2, 3, 7, 8, config)
        )


@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_family_boundary_and_clipped_classification_match_frozen_hex_path(orientation):
    geometry = DesignerProjectShell.create("landscape", "m", orientation).geometry
    assert physical_perimeter_rings(geometry, 2) == _legacy_rings(geometry, 2)
    clipped_ids = {
        f"placement-{index:06d}" for index, value in enumerate(geometry.placements)
        if value.piece_type not in {"full", "outside"}
    }
    assert set(build_border_layer(geometry, "none").protected_placement_ids) == clipped_ids


def test_generic_boundary_detection_uses_supplied_topology_degree():
    geometry = DesignerProjectShell.create("square", "m").geometry
    hex_topology = get_tile_family_for_geometry_shape(geometry.shape).topology

    class HighDegreeTopology:
        expected_neighbor_degree = 100

        def neighbors(self, row, column, rows, columns, orientation_id):
            return hex_topology.neighbors(row, column, rows, columns, orientation_id)

    first_ring = set(physical_perimeter_rings(geometry, 1, HighDegreeTopology())[0])
    full = {
        (value.row, value.column) for value in geometry.placements
        if value.piece_type == "full"
    }
    assert first_ring == full
    assert "< 6" not in getsource(border_module.physical_perimeter_rings)


def test_hex_family_supports_exact_current_border_catalog_only():
    family = get_tile_family("hexagon")
    assert family.supported_border_presets() == tuple(value.id for value in BORDER_PRESETS)
    square = get_tile_family_for_geometry_shape("square")
    assert square.supported_border_presets() == ("none", "solid")


@pytest.mark.parametrize("preset", ("none", "solid", "double", "alternating"))
@pytest.mark.parametrize("orientation", ("point_top", "flat_top"))
def test_all_hex_border_results_remain_deterministic(preset, orientation):
    geometry = DesignerProjectShell.create("portrait", "s", orientation).geometry
    assert build_border_layer(geometry, preset) == build_border_layer(geometry, preset)

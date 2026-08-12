from io import BytesIO
import json

import pytest

from mosaic_engine.border import (
    BORDER_PRESETS,
    PROJECT_COLOR_ROLES,
    build_border_layer,
    physical_perimeter_rings,
)
from mosaic_engine.designer import DesignerProjectShell, MosaicDesignerApp
from mosaic_engine.model import MosaicConfig
from mosaic_engine.processing import tile_neighbors


def _request(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    captured = {}
    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)
    response = b"".join(app({
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": BytesIO(raw),
    }, start_response))
    if captured["headers"]["Content-Type"].startswith("application/json"):
        response = json.loads(response)
    else:
        response = response.decode()
    return captured["status"], response


def _shell(canvas="square-s", tile="m"):
    return DesignerProjectShell.create(canvas, tile)


def _coordinates(geometry, tile_ids):
    result = set()
    for tile_id in tile_ids:
        index = int(tile_id.rsplit("-", 1)[-1])
        result.add((index // geometry.columns, index % geometry.columns))
    return result


def _neighbors(geometry, coordinate):
    config = MosaicConfig(tile_shape="hex", hex_orientation="pointy")
    return set(tile_neighbors(
        coordinate[0], coordinate[1], geometry.rows, geometry.columns, config,
    ))


def test_curated_border_preset_definitions_are_fixed_and_deterministic():
    assert [value.id for value in BORDER_PRESETS] == [
        "none", "solid", "double", "alternating",
    ]
    assert [value.depth for value in BORDER_PRESETS] == [0, 1, 2, 1]
    assert BORDER_PRESETS[1].pattern_roles == ("border_primary",)
    assert BORDER_PRESETS[2].pattern_roles == (
        "border_primary", "border_secondary",
    )
    assert BORDER_PRESETS == tuple(BORDER_PRESETS)
    assert all(not hasattr(value, "user_width") for value in BORDER_PRESETS)


def test_none_protects_only_clipped_perimeter_with_edge_role():
    shell = _shell()
    state = build_border_layer(shell.geometry, "none")
    clipped = {
        f"placement-{index:06d}"
        for index, value in enumerate(shell.geometry.placements)
        if value.piece_type not in {"full", "outside"}
    }
    full = {
        f"placement-{index:06d}"
        for index, value in enumerate(shell.geometry.placements)
        if value.piece_type == "full"
    }
    assert set(state.protected_placement_ids) == clipped
    assert set(state.available_artwork_placement_ids) == full
    assert {value.color_role for value in state.assignments} == {"edge"}
    assert not clipped & set(state.available_artwork_placement_ids)


def test_solid_owns_one_full_physical_ring_plus_clipped_perimeter():
    shell = _shell()
    state = build_border_layer(shell.geometry, "solid")
    first_ring = set(physical_perimeter_rings(shell.geometry, 1)[0])
    owned = _coordinates(shell.geometry, state.border_owned_placement_ids)
    clipped = {
        (value.row, value.column) for value in shell.geometry.placements
        if value.piece_type not in {"full", "outside"}
    }
    assert owned == first_ring | clipped
    assert state.protected_placement_ids == state.border_owned_placement_ids
    assert {value.color_role for value in state.assignments} == {"border_primary"}
    assert set(state.available_artwork_placement_ids).isdisjoint(
        state.border_owned_placement_ids
    )


def test_double_owns_two_adjacency_derived_rings_and_leaves_third_available():
    shell = _shell()
    rings = physical_perimeter_rings(shell.geometry, 3)
    state = build_border_layer(shell.geometry, "double")
    first, second, third = map(set, rings)
    layers = tuple(_coordinates(shell.geometry, value) for value in state.layer_placement_ids)
    clipped = {
        (value.row, value.column) for value in shell.geometry.placements
        if value.piece_type not in {"full", "outside"}
    }
    assert layers[0] == first | clipped
    assert layers[1] == second
    assert all(_neighbors(shell.geometry, value) & first for value in second)
    assert {
        value.color_role for value in state.assignments if value.layer == 0
    } == {"border_primary"}
    assert {
        value.color_role for value in state.assignments if value.layer == 1
    } == {"border_secondary"}
    available = _coordinates(shell.geometry, state.available_artwork_placement_ids)
    assert third <= available


def test_alternating_uses_one_continuous_deterministic_perimeter_sequence():
    shell = _shell("landscape", "s")
    first = build_border_layer(shell.geometry, "alternating")
    second = build_border_layer(shell.geometry, "alternating")
    assert first == second
    roles = {value.tile_id: value.color_role for value in first.assignments}
    sequence = [roles[tile_id] for tile_id in first.perimeter_order]
    assert sequence
    assert all(
        value == ("border_primary" if index % 2 == 0 else "border_secondary")
        for index, value in enumerate(sequence)
    )
    assert set(sequence) == {"border_primary", "border_secondary"}


def test_available_tile_touching_border_remains_available_without_buffer():
    shell = _shell()
    state = build_border_layer(shell.geometry, "solid")
    protected = _coordinates(shell.geometry, state.protected_placement_ids)
    available = _coordinates(shell.geometry, state.available_artwork_placement_ids)
    touching = {
        value for value in available
        if _neighbors(shell.geometry, value) & protected
    }
    assert touching
    assert touching <= available


def test_switching_is_clean_reversible_and_does_not_mutate_geometry():
    shell = _shell()
    geometry = shell.geometry
    polygons = tuple(value.vertices_in for value in geometry.placements)
    solid = shell.with_border("solid")
    double = solid.with_border("double")
    none = double.with_border("none")
    solid_again = none.with_border("solid")
    assert build_border_layer(solid.geometry, "solid") == build_border_layer(
        solid_again.geometry, "solid"
    )
    assert set(build_border_layer(none.geometry, "none").protected_placement_ids) < set(
        build_border_layer(double.geometry, "double").protected_placement_ids
    )
    assert shell.geometry is geometry
    assert solid_again.geometry is geometry
    assert tuple(value.vertices_in for value in geometry.placements) == polygons


@pytest.mark.parametrize("canvas,tile", [
    ("square-s", "s"),
    ("landscape", "m"),
    ("panoramic", "l"),
])
def test_representative_geometries_are_deterministic_and_exclude_outside(
    canvas, tile,
):
    shell = _shell(canvas, tile)
    polygons = tuple(value.vertices_in for value in shell.geometry.placements)
    for preset in ("none", "solid", "double", "alternating"):
        first = build_border_layer(shell.geometry, preset)
        second = build_border_layer(shell.geometry, preset)
        assert first == second
        assert not {
            f"placement-{index:06d}"
            for index, value in enumerate(shell.geometry.placements)
            if value.piece_type == "outside"
        } & set(first.border_owned_placement_ids)
        assert tuple(value.vertices_in for value in shell.geometry.placements) == polygons


def test_border_roles_are_semantic_and_not_a_manufacturing_palette():
    assert set(PROJECT_COLOR_ROLES) == {
        "background", "edge", "border_primary", "border_secondary",
    }
    assert all(not hasattr(value, "palette") for value in BORDER_PRESETS)


def test_designer_border_api_switches_without_stale_state():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _, payload = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    none_ids = set(payload["project"]["border"]["protected_placement_ids"])
    assert len(payload["border_presets"]) == 4

    _, solid = _request(
        app, "POST", "/api/designer/border", {"preset_id": "solid"},
    )
    _, double = _request(
        app, "POST", "/api/designer/border", {"preset_id": "double"},
    )
    _, none = _request(
        app, "POST", "/api/designer/border", {"preset_id": "none"},
    )
    assert none["border"]["preset_id"] == "none"
    assert set(none["border"]["protected_placement_ids"]) == none_ids
    assert solid["border"]["counts"]["protected"] < (
        double["border"]["counts"]["protected"]
    )
    assert none["document"]["dirty"] is True


def test_border_api_rejects_unknown_preset_atomically():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    before = app.project
    status, payload = _request(
        app, "POST", "/api/designer/border", {"preset_id": "custom"},
    )
    assert status == "400 Bad Request"
    assert "Unknown border preset" in payload["error"]
    assert app.project is before


def test_border_inspector_and_frontend_use_backend_membership():
    app = MosaicDesignerApp()
    status, html = _request(app, "GET", "/")
    assert status == "200 OK"
    assert 'id="border-presets"' in html
    assert 'id="border-lock-state"' in html
    assert "Coming later</span></div>" not in html
    _, script = _request(app, "GET", "/designer.js")
    assert 'performDesignerMutation("/api/designer/border"' in script
    assert "tile.border_owned" in script
    assert "tile.artwork_available" in script
    assert "tile.display_color" in script
    assert "tile.color_role" not in script
    assert "tile_neighbors" not in script
    assert "physical_perimeter" not in script
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert ".border-presets" in stylesheet
    assert ".border-preview.solid" in stylesheet
    assert ".border-preview.double" in stylesheet
    assert ".border-preview.alternating" in stylesheet
    assert "var(--border-primary)" in stylesheet
    assert "var(--border-secondary)" in stylesheet
    assert 'role_to_color_id.border_primary' in script
    assert 'role_to_color_id.border_secondary' in script
    assert ".border-control { container-type: inline-size" in stylesheet
    assert "@container (max-width: 16.5rem)" in stylesheet
    assert ".border-presets { grid-template-columns: minmax(0, 1fr); }" in stylesheet
    assert ".border-preset > span:last-child { min-width: 0; overflow-wrap: anywhere; }" in stylesheet
    assert "min-width: 1.55rem; flex: none" in stylesheet

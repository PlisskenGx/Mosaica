from io import BytesIO
import json

import pytest

import mosaic_engine.designer as designer_module
import mosaic_engine.designer_generation as generation_module
from mosaic_engine.artwork import ArtworkTransform
from mosaic_engine.border import build_border_layer
from mosaic_engine.designer import MosaicDesignerApp
from mosaic_engine.designer_colors import DEFAULT_DESIGNER_COLORS


BLUE = "#0066CC"


@pytest.fixture(autouse=True)
def _compact_test_raster(monkeypatch):
    monkeypatch.setattr(generation_module, "SVG_RASTER_WIDTH", 512)


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
    return captured["status"], json.loads(response)


def _app(border="none"):
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    if border != "none":
        _request(app, "POST", "/api/designer/border", {"preset_id": border})
    return app


def _svg(body, view_box="0 0 100 100"):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">{body}</svg>'


def _stripe_svg(count, width=100, reuse_semantic=False):
    generated = [
        f"#{index + 1:02X}{(index * 37 + 11) % 256:02X}{(index * 71 + 19) % 256:02X}"
        for index in range(count)
    ]
    colors = (
        ["#FAF9F6", "#34373D", "#A87655", *generated[:count - 3]]
        if reuse_semantic and count >= 3 else generated
    )
    return _svg("".join(
        f'<rect x="{index * width / count}" width="{width / count + .01}" '
        f'height="100" fill="{color}"/>'
        for index, color in enumerate(colors)
    ), view_box=f"0 0 {width} 100")


def _upload(app, svg):
    return _request(app, "POST", "/api/designer/artwork/upload", {
        "filename": "mark.svg", "svg_content": svg,
    })


def _generate(app):
    status, payload = _request(
        app, "POST", "/api/designer/artwork/generate", {},
    )
    return (status, app.payload()) if status == "200 OK" else (status, payload)


def _generate_result(app):
    return _request(app, "POST", "/api/designer/artwork/generate", {})


def test_upload_is_placement_only_and_explicit_generation_keeps_source():
    app = _app()
    source = _svg(f'<circle cx="50" cy="50" r="45" fill="{BLUE}"/>')
    status, uploaded = _upload(app, source)
    assert status == "200 OK"
    assert uploaded["payload_kind"] == "artwork_state"
    assert uploaded["generated_artwork"] is None
    assert app.generated_artwork is None
    sanitized = app.artwork.sanitized_svg

    status, payload = _generate(app)
    assert status == "200 OK"
    generated = payload["project"]["generated_artwork"]
    assert generated["current"] is True
    assert generated["assignment_count"] > 0
    assert generated["revision"] == 1
    assert app.artwork.sanitized_svg == sanitized
    assert payload["project"]["artwork"]["edit_mode"] is False


def test_generation_honors_physical_transform_and_is_deterministic():
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    transform = {"x_in": 0, "y_in": 0, "width_in": 24, "height_in": 24}
    _request(app, "POST", "/api/designer/artwork/transform", transform)
    _, first = _generate(app)
    first_ids = [
        value["tile_id"]
        for value in first["project"]["generated_artwork"]["assignments"]
    ]
    available = set(first["project"]["border"]["available_artwork_placement_ids"])
    assert set(first_ids) == available

    _request(app, "POST", "/api/designer/artwork/edit", {})
    _request(app, "POST", "/api/designer/artwork/transform", {
        **transform, "x_in": 30,
    })
    assert app.generated_artwork.stale is True
    old_assignments = app.generated_artwork.assignments
    _, moved = _generate(app)
    assert moved["project"]["generated_artwork"]["assignment_count"] == 0
    assert app.generated_artwork.assignments != old_assignments

    _, repeated = _generate(app)
    assert repeated["project"]["generated_artwork"]["assignments"] == (
        moved["project"]["generated_artwork"]["assignments"]
    )


def test_only_available_full_tiles_receive_generated_assignments():
    app = _app("double")
    _upload(app, _svg('<rect width="100" height="100" fill="#A87655"/>'))
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": -20, "y_in": -20, "width_in": 64, "height_in": 64,
    })
    _, payload = _generate(app)
    project = payload["project"]
    generated_ids = {
        value["tile_id"] for value in project["generated_artwork"]["assignments"]
    }
    available = set(project["border"]["available_artwork_placement_ids"])
    protected = set(project["border"]["protected_placement_ids"])
    assert generated_ids == available
    assert generated_ids.isdisjoint(protected)
    assert all(
        not tile["generated_artwork"]
        for tile in project["geometry"]["tiles"]
        if tile["piece_type"] != "full" or tile["protected"]
    )
    assert any(
        tile["generated_artwork"] and tile["artwork_available"]
        for tile in project["geometry"]["tiles"]
    )


@pytest.mark.parametrize("body,expected", [
    ('<rect width="100" height="100" fill="#0066cc"/>', 1),
    ('<rect width="50" height="100" fill="#0066cc"/><rect x="50" width="50" height="100" fill="#cc0000"/>', 2),
    ('<rect width="34" height="100" fill="#faf9f6"/><rect x="34" width="33" height="100" fill="#0066cc"/><rect x="67" width="33" height="100" fill="#cc0000"/>', 3),
    ('<rect width="25" height="100" fill="#faf9f6"/><rect x="25" width="25" height="100" fill="#d8d6cf"/><rect x="50" width="25" height="100" fill="#34373d"/><rect x="75" width="25" height="100" fill="#a87655"/>', 4),
])
def test_discrete_effective_colors_are_added_to_project_palette(body, expected):
    app = _app()
    _upload(app, _svg(body))
    status, payload = _generate(app)
    assert status == "200 OK"
    assert payload["project"]["generated_artwork"]["source_color_count"] == expected
    assert len(payload["project"]["color_system"]["design_colors"]) >= 3


def test_five_effective_colors_generate_without_manufacturing_limit():
    colors = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff"]
    stripes = "".join(
        f'<rect x="{index * 20}" width="20" height="100" fill="{color}"/>'
        for index, color in enumerate(colors)
    )
    app = _app()
    _upload(app, _svg(stripes))
    status, payload = _generate(app)
    assert status == "200 OK"
    assert payload["project"]["generated_artwork"]["source_color_count"] == 5
    assert len(payload["project"]["color_system"]["design_colors"]) > 4
    counts = payload["project"]["color_counts"]
    assert len(counts) > 4
    assert sum(value["count"] for value in counts) == (
        payload["project"]["geometry"]["visible_piece_count"]
    )


def test_eight_color_artwork_generates_with_stable_project_ids():
    app = _app("double")
    _upload(app, _stripe_svg(8))
    status, payload = _generate(app)
    assert status == "200 OK"
    generated = payload["project"]["generated_artwork"]
    assert generated["source_color_count"] == 8
    artwork_ids = {
        value["color_id"] for value in generated["assignments"]
    }
    assert len(artwork_ids) == 8
    assert all(value.startswith("project-color-") for value in artwork_ids)
    assert len(payload["project"]["color_system"]["design_colors"]) == 11
    assert sum(
        value["count"] for value in payload["project"]["color_counts"]
    ) == payload["project"]["geometry"]["visible_piece_count"]


def test_32_color_safety_limit_is_not_a_manufacturing_limit():
    new_colors = tuple(
        (index + 1, (index * 37 + 11) % 256, (index * 71 + 19) % 256)
        for index in range(30)
    )
    colors = ((250, 249, 246), (52, 55, 61), (168, 118, 85), *new_colors[:29])
    mapping, resolution = generation_module._allocate_colors(
        colors, DEFAULT_DESIGNER_COLORS,
    )
    assert len(mapping) == 32
    assert len(resolution.colors) == 32
    with pytest.raises(ValueError, match="more than 32 distinct colors"):
        generation_module._allocate_colors(
            (*colors, new_colors[29]), DEFAULT_DESIGNER_COLORS,
        )


def test_project_color_ids_reuse_equivalents_and_append_on_regeneration():
    app = _app()
    _upload(app, _svg(
        '<rect width="50" height="100" fill="#ff0000"/>'
        '<rect x="50" width="50" height="100" fill="#0066cc"/>'
    ))
    _generate(app)
    first_colors = app.project.color_system.colors
    first_by_hex = {value.display_color: value.color_id for value in first_colors}

    _request(app, "POST", "/api/designer/artwork/replace", {
        "filename": "replacement.svg",
        "svg_content": _svg(
            '<rect width="50" height="100" fill="rgb(255, 0, 0)"/>'
            '<rect x="50" width="50" height="100" fill="#00ff00"/>'
        ),
    })
    _generate(app)
    second = app.project.color_system
    second_by_hex = {value.display_color: value.color_id for value in second.colors}
    assert second_by_hex["#FF0000"] == first_by_hex["#FF0000"]
    assert second_by_hex["#0066CC"] == first_by_hex["#0066CC"]
    assert second_by_hex["#00FF00"] == f"project-color-{len(first_colors) + 1}"
    assert [value.color_id for value in second.colors[:len(first_colors)]] == [
        value.color_id for value in first_colors
    ]
    counts = app.payload()["project"]["color_counts"]
    assert first_by_hex["#0066CC"] not in {
        value["color_id"] for value in counts
    }


def test_failed_regeneration_does_not_mutate_palette_or_previous_result(
    monkeypatch,
):
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    _generate(app)
    previous_palette = app.project.color_system
    previous_generated = app.generated_artwork
    _request(app, "POST", "/api/designer/artwork/edit", {})
    transform = app.artwork.transform
    _request(app, "POST", "/api/designer/artwork/transform", {
        **transform.to_dict(), "x_in": transform.x_in + 1,
    })
    stale_generated = app.generated_artwork

    def fail_generation(*args, **kwargs):
        raise ValueError("Synthetic deterministic generation failure.")

    monkeypatch.setattr(designer_module, "generate_designer_artwork", fail_generation)
    status, payload = _generate_result(app)
    assert status == "400 Bad Request"
    assert "Synthetic deterministic" in payload["error"]
    assert app.project.color_system is previous_palette
    assert app.generated_artwork is stale_generated
    assert app.generated_artwork.assignments == previous_generated.assignments


def test_source_color_without_generated_tiles_does_not_enter_palette():
    app = _app("solid")
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    protected_id = app.payload()["project"]["border"][
        "protected_placement_ids"
    ][0]
    protected = next(
        value for index, value in enumerate(app.project.geometry.placements)
        if f"placement-{index:06d}" == protected_id
    )
    x, y = protected.visible_centroid_in
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": x - .025, "y_in": y - .025,
        "width_in": .05, "height_in": .05,
    })
    status, payload = _generate(app)
    assert status == "200 OK"
    assert payload["project"]["generated_artwork"]["assignment_count"] == 0
    assert payload["project"]["generated_artwork"]["source_color_count"] == 0
    assert len(payload["project"]["color_system"]["design_colors"]) == 3


def test_transparency_and_equivalent_color_syntax_are_normalized():
    app = _app()
    _upload(app, _svg(
        '<rect width="100" height="100" fill="red" fill-opacity="0"/>'
        '<rect width="50" height="100" fill="#ff0000"/>'
        '<rect x="50" width="50" height="100" fill="rgb(255, 0, 0)"/>'
    ))
    status, payload = _generate(app)
    assert status == "200 OK"
    assert payload["project"]["generated_artwork"]["source_colors"] == [[255, 0, 0]]


def test_active_gradient_is_rejected_without_quantization():
    app = _app()
    _upload(app, _svg(
        '<defs><linearGradient id="g"><stop stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs>'
        '<rect width="100" height="100" fill="url(#g)"/>'
    ))
    status, payload = _generate(app)
    assert status == "400 Bad Request"
    assert "Gradient" in payload["error"]
    assert app.generated_artwork is None


def test_alternating_border_and_multicolor_artwork_have_independent_ids():
    app = _app("alternating")
    _upload(app, _svg(
        '<rect width="50" height="100" fill="#0066cc"/>'
        '<rect x="50" width="50" height="100" fill="#cc0000"/>'
    ))
    status, payload = _generate(app)
    assert status == "200 OK"
    colors = payload["project"]["color_system"]["design_colors"]
    assert len(colors) == 5
    assert payload["project"]["color_system"]["role_to_color_id"] == {
        "background": "project-color-1",
        "border_primary": "project-color-2",
        "border_secondary": "project-color-3",
        "edge": "project-color-1",
    }
    assert [value["display_color"] for value in colors[:3]] == [
        "#FAF9F6", "#34373D", "#A87655",
    ]


def test_strongest_qualifying_source_color_wins_one_tile_deterministically():
    app = _app()
    _upload(app, _svg(
        '<rect width="60" height="100" fill="#0066cc"/>'
        '<rect x="60" width="40" height="100" fill="#cc0000"/>'
    ))
    placement = next(
        value for value in app.project.geometry.placements
        if value.piece_type == "full"
    )
    xs = [value[0] for value in placement.vertices_in]
    ys = [value[1] for value in placement.vertices_in]
    app.artwork = app.artwork.__class__(
        **{
            **app.artwork.__dict__,
            "transform": ArtworkTransform(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
        }
    )
    _, payload = _generate(app)
    assignment = next(
        value for value in payload["project"]["generated_artwork"]["assignments"]
        if value["row"] == placement.row and value["column"] == placement.column
    )
    assert assignment["source_rgb"] == [0, 102, 204]
    assert assignment["coverage"] >= 0.45


def test_partial_coverage_uses_established_threshold():
    app = _app()
    _upload(app, _svg(f'<rect width="40" height="100" fill="{BLUE}"/>'))
    placement = next(
        value for value in app.project.geometry.placements
        if value.piece_type == "full"
    )
    xs = [value[0] for value in placement.vertices_in]
    ys = [value[1] for value in placement.vertices_in]
    app.artwork = app.artwork.__class__(
        **{
            **app.artwork.__dict__,
            "transform": ArtworkTransform(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
        }
    )
    _, payload = _generate(app)
    assert not any(
        value["row"] == placement.row and value["column"] == placement.column
        for value in payload["project"]["generated_artwork"]["assignments"]
    )
    assert payload["project"]["generated_artwork"]["coverage_threshold"] == 0.45


def test_proportional_scaling_changes_generated_region_without_viewport_input():
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    original = app.artwork.transform
    _, small = _generate(app)
    small_count = small["project"]["generated_artwork"]["assignment_count"]
    _request(app, "POST", "/api/designer/artwork/edit", {})
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": original.x_in - original.width_in / 2,
        "y_in": original.y_in - original.height_in / 2,
        "width_in": original.width_in * 2,
        "height_in": original.height_in * 2,
    })
    _, large = _generate(app)
    assert large["project"]["generated_artwork"]["assignment_count"] > small_count
    assert "viewport" not in app.artwork.transform.to_dict()


def test_nonproportional_width_and_height_change_generated_coverage():
    app = _app()
    _upload(app, _svg(f'<circle cx="50" cy="50" r="45" fill="{BLUE}"/>'))
    initial = app.artwork.transform
    _, baseline = _generate(app)
    baseline_tiles = {
        (value["row"], value["column"])
        for value in baseline["project"]["generated_artwork"]["assignments"]
    }

    _request(app, "POST", "/api/designer/artwork/edit", {})
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": initial.x_in - initial.width_in / 2,
        "y_in": initial.y_in,
        "width_in": initial.width_in * 2,
        "height_in": initial.height_in,
    })
    assert app.generated_artwork.stale is True
    stale = app.payload()["project"]
    assert any(value["generated_artwork"] for value in stale["geometry"]["tiles"])
    _, horizontal = _generate(app)
    horizontal_tiles = {
        (value["row"], value["column"])
        for value in horizontal["project"]["generated_artwork"]["assignments"]
    }
    assert len(horizontal_tiles) > len(baseline_tiles)
    assert len({column for _, column in horizontal_tiles}) > len({column for _, column in baseline_tiles})

    _request(app, "POST", "/api/designer/artwork/edit", {})
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": initial.x_in,
        "y_in": initial.y_in - initial.height_in / 2,
        "width_in": initial.width_in,
        "height_in": initial.height_in * 2,
    })
    _, vertical = _generate(app)
    vertical_tiles = {
        (value["row"], value["column"])
        for value in vertical["project"]["generated_artwork"]["assignments"]
    }
    assert len(vertical_tiles) > len(baseline_tiles)
    assert len({row for row, _ in vertical_tiles}) > len({row for row, _ in baseline_tiles})


def test_distorted_transform_compact_reconciliation_and_border_protection():
    app = _app("double")
    frontend = app.payload()
    _, response = _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    frontend = _apply_frontend_state(frontend, response)
    distorted = {
        "x_in": -4.25, "y_in": 2.75, "width_in": 31.5, "height_in": 7.125,
    }
    status, response = _request(
        app, "POST", "/api/designer/artwork/transform", distorted,
    )
    assert status == "200 OK" and response["payload_kind"] == "artwork_state"
    frontend = _apply_frontend_state(frontend, response)
    assert frontend["project"]["artwork"]["transform"] == distorted
    status, response = _generate_result(app)
    frontend = _apply_frontend_state(frontend, response)
    assert frontend["project"]["artwork"]["transform"] == distorted
    protected = set(frontend["project"]["border"]["protected_placement_ids"])
    assert all(
        not tile["generated_artwork"]
        for tile in frontend["project"]["geometry"]["tiles"]
        if tile["id"] in protected
    )


def test_stale_state_edit_mode_border_filtering_and_atomic_regeneration():
    app = _app()
    _upload(app, _svg(
        '<rect width="50" height="100" fill="#0066cc"/>'
        '<rect x="50" width="50" height="100" fill="#cc0000"/>'
    ))
    _, valid = _generate(app)
    original = app.generated_artwork
    assert valid["project"]["artwork"]["edit_mode"] is False

    _, edited = _request(app, "POST", "/api/designer/artwork/edit", {})
    assert edited["artwork"]["edit_mode"] is True
    assert app.generated_artwork is original
    transform = app.artwork.transform
    _request(app, "POST", "/api/designer/artwork/transform", {
        **transform.to_dict(), "x_in": transform.x_in + 1,
    })
    assert app.generated_artwork.stale is True
    assert app.generated_artwork.assignments == original.assignments

    _request(app, "POST", "/api/designer/border", {"preset_id": "alternating"})
    stale_payload = app.payload()["project"]
    protected = set(stale_payload["border"]["protected_placement_ids"])
    assert stale_payload["generated_artwork"]["needs_regeneration"] is True
    assert all(
        not tile["generated_artwork"]
        for tile in stale_payload["geometry"]["tiles"]
        if tile["id"] in protected
    )
    status, regenerated = _generate(app)
    assert status == "200 OK"
    assert regenerated["project"]["generated_artwork"]["current"] is True
    assert app.generated_artwork.assignments != original.assignments


def _assert_effective_generated_precedence(app, stored_ids):
    project = app.payload()["project"]
    available = set(project["border"]["available_artwork_placement_ids"])
    protected = set(project["border"]["protected_placement_ids"])
    tiles = {value["id"]: value for value in project["geometry"]["tiles"]}
    expected_visible = stored_ids & available
    assert {
        tile_id for tile_id, tile in tiles.items()
        if tile["generated_artwork"]
    } == expected_visible
    assert all(not tiles[tile_id]["generated_artwork"] for tile_id in protected)
    assert project["generated_artwork"]["exists"] is True
    assert project["generated_artwork"]["current"] is False
    assert project["generated_artwork"]["display_active"] is True
    assert sum(value["count"] for value in project["color_counts"]) == (
        project["geometry"]["visible_piece_count"]
    )
    expected_counts = {}
    for tile in tiles.values():
        expected_counts[tile["color_id"]] = (
            expected_counts.get(tile["color_id"], 0) + 1
        )
    assert {
        value["color_id"]: value["count"]
        for value in project["color_counts"]
    } == expected_counts
    return project


def test_generated_none_to_solid_stays_visible_beneath_new_border():
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": -20, "y_in": -20, "width_in": 64, "height_in": 64,
    })
    _generate(app)
    stored_ids = {value.tile_id for value in app.generated_artwork.assignments}
    status, response = _request(
        app, "POST", "/api/designer/border", {"preset_id": "solid"},
    )
    assert status == "200 OK" and response["payload_kind"] == "design_state"
    project = _assert_effective_generated_precedence(app, stored_ids)
    protected = set(project["border"]["protected_placement_ids"])
    assert stored_ids & protected
    assert stored_ids & set(project["border"]["available_artwork_placement_ids"])


def test_generated_solid_to_none_keeps_stored_assignments_only():
    app = _app("solid")
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": -20, "y_in": -20, "width_in": 64, "height_in": 64,
    })
    _generate(app)
    stored_ids = {value.tile_id for value in app.generated_artwork.assignments}
    old_available = set(
        app.payload()["project"]["border"]["available_artwork_placement_ids"]
    )
    _request(app, "POST", "/api/designer/border", {"preset_id": "none"})
    project = _assert_effective_generated_precedence(app, stored_ids)
    newly_available = (
        set(project["border"]["available_artwork_placement_ids"])
        - old_available
    )
    tiles = {value["id"]: value for value in project["geometry"]["tiles"]}
    assert newly_available
    assert all(
        not tiles[tile_id]["generated_artwork"]
        and tiles[tile_id]["color_role"] == "background"
        for tile_id in newly_available
    )


def test_stale_generated_layer_survives_every_border_switch_and_regenerates():
    app = _app()
    _upload(app, _svg(f'<circle cx="50" cy="50" r="43" fill="{BLUE}"/>'))
    _generate(app)
    stored_ids = {value.tile_id for value in app.generated_artwork.assignments}
    assert stored_ids
    for preset in ("solid", "double", "alternating", "none"):
        status, response = _request(
            app, "POST", "/api/designer/border", {"preset_id": preset},
        )
        assert status == "200 OK"
        assert response["generated_artwork"]["exists"] is True
        project = _assert_effective_generated_precedence(app, stored_ids)
        assert project["border"]["preset_id"] == preset

    status, _ = _generate(app)
    assert status == "200 OK"
    regenerated = app.payload()["project"]["generated_artwork"]
    assert regenerated["exists"] is True
    assert regenerated["current"] is True
    assert regenerated["revision"] == 2


def test_none_has_only_clipped_edge_ownership_with_edge_near_artwork():
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    _request(app, "POST", "/api/designer/artwork/transform", {
        "x_in": -20, "y_in": -20, "width_in": 64, "height_in": 64,
    })
    _generate(app)
    generated_ids = {value.tile_id for value in app.generated_artwork.assignments}
    project = app.payload()["project"]
    tiles = project["geometry"]["tiles"]
    clipped = [value for value in tiles if value["piece_type"] != "full"]
    full = [value for value in tiles if value["piece_type"] == "full"]

    assert clipped and full
    assert all(
        value["protected"]
        and not value["artwork_available"]
        and value["color_role"] == "edge"
        and not value["generated_artwork"]
        for value in clipped
    )
    assert all(
        not value["border_owned"] and value["artwork_available"]
        for value in full
    )
    assert generated_ids == {value["id"] for value in full}


def test_semantic_color_identities_are_immutable_when_generating_under_solid():
    app = _app("solid")
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    status, payload = _generate(app)
    assert status == "200 OK"
    colors = {
        value["color_id"]: value
        for value in payload["project"]["color_system"]["design_colors"]
    }
    assert colors["project-color-1"]["display_color"] == "#FAF9F6"
    assert colors["project-color-2"]["display_color"] == "#34373D"
    assert colors["project-color-3"]["display_color"] == "#A87655"
    assert colors["project-color-4"]["display_color"] == BLUE
    assert {
        value.color_id for value in app.generated_artwork.assignments
    } == {"project-color-4"}

    _request(app, "POST", "/api/designer/border", {"preset_id": "none"})
    none = app.payload()["project"]
    clipped = [
        value for value in none["geometry"]["tiles"]
        if value["piece_type"] != "full"
    ]
    assert all(
        value["color_role"] == "edge"
        and value["color_id"] == "project-color-1"
        and value["display_color"] == "#FAF9F6"
        for value in clipped
    )


@pytest.mark.parametrize("transform", [
    {"x_in": 7, "y_in": 7, "width_in": 10, "height_in": 10},
    {"x_in": 0, "y_in": 0, "width_in": 12, "height_in": 12},
    {"x_in": -6, "y_in": -6, "width_in": 36, "height_in": 36},
])
def test_none_edge_invariants_survive_repeated_border_switches(transform):
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    _request(app, "POST", "/api/designer/artwork/transform", transform)
    _generate(app)
    stored_ids = {value.tile_id for value in app.generated_artwork.assignments}

    for preset in ("solid", "none", "double", "none", "alternating", "none"):
        _request(app, "POST", "/api/designer/border", {"preset_id": preset})
        if preset != "none":
            continue
        project = _assert_effective_generated_precedence(app, stored_ids)
        tiles = project["geometry"]["tiles"]
        assert all(
            value["protected"]
            and value["color_role"] == "edge"
            and not value["generated_artwork"]
            for value in tiles if value["piece_type"] != "full"
        )
        assert all(
            not value["border_owned"] and value["artwork_available"]
            for value in tiles if value["piece_type"] == "full"
        )


def test_reset_replace_and_remove_generation_state_transitions():
    app = _app()
    _upload(app, _svg(f'<circle cx="50" cy="50" r="45" fill="{BLUE}"/>'))
    _generate(app)
    source = app.artwork.sanitized_svg
    _request(app, "POST", "/api/designer/artwork/reset", {})
    assert app.generated_artwork.stale is True
    assert app.artwork.sanitized_svg == source
    _request(app, "POST", "/api/designer/artwork/replace", {
        "filename": "new.svg",
        "svg_content": _svg('<rect width="100" height="100" fill="#34373d"/>'),
    })
    assert app.generated_artwork is None
    _generate(app)
    _request(app, "POST", "/api/designer/artwork/remove", {})
    assert app.artwork is None and app.generated_artwork is None


def test_generated_counts_are_backend_authoritative_and_reconcile():
    app = _app("solid")
    _upload(app, _svg(f'<circle cx="50" cy="50" r="45" fill="{BLUE}"/>'))
    _, payload = _generate(app)
    project = payload["project"]
    assert sum(value["count"] for value in project["color_counts"]) == (
        project["geometry"]["visible_piece_count"]
    )
    generated_id = project["generated_artwork"]["assignments"][0]["color_id"]
    generated_count = sum(
        tile["generated_artwork"] for tile in project["geometry"]["tiles"]
    )
    assert next(
        value["count"] for value in project["color_counts"]
        if value["color_id"] == generated_id
    ) == generated_count


def test_frontend_exposes_explicit_generation_without_client_classification():
    app = _app()
    status, html = _asset(app, "/")
    assert status == "200 OK"
    assert "Generate Mosaic" in html and "Edit Artwork" in html
    _, script = _asset(app, "/designer.js")
    assert '{ requireGenerated: true, name: "Generate Mosaic" }' in script
    assert 'artwork-generate' in script
    assert '"Generating…"' in script
    assert "generationInFlight" in script
    assert "if (generationInFlight) return" in script
    assert "validateDesignerState" in script
    assert "applyDesignerState(payload" in script
    assert "performDesignerMutation" in script
    assert "const previousState = state" in script
    assert "state = previousState" in script
    assert "finally" in script
    assert "syncGenerationControl()" in script
    assert 'button.setAttribute("aria-busy", String(generationInFlight))' in script
    assert 'button.disabled = generationInFlight' in script
    assert "project.color_counts" in script
    assert "coverage" not in script.lower()
    assert "quantiz" not in script.lower()


def test_generation_response_is_compact_and_authoritative():
    app = _app("solid")
    _upload(app, _svg(f'<circle cx="50" cy="50" r="45" fill="{BLUE}"/>'))
    _, before = _request(app, "GET", "/api/designer")
    status, generated = _generate_result(app)
    assert status == "200 OK"
    assert generated["payload_kind"] == "design_state"
    assert generated["generated_artwork"]["current"] is True
    assert generated["artwork"]["edit_mode"] is False
    assert generated["color_counts"] != before["project"]["color_counts"]
    assert generated["tile_updates"]
    assert "project" not in generated and "geometry" not in generated
    assert "assignments" not in generated["generated_artwork"]
    assert "vertices_in" not in json.dumps(generated)


def test_designer_assets_are_not_cached_during_local_ui_development():
    app = _app()
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    app({
        "REQUEST_METHOD": "GET", "PATH_INFO": "/designer.js",
        "CONTENT_LENGTH": "0", "wsgi.input": BytesIO(),
    }, start_response)
    assert captured["status"] == "200 OK"
    assert captured["headers"]["Cache-Control"] == "no-store"


def _assert_workspace_state(status, payload):
    assert status == "200 OK"
    assert payload["stage"] == "workspace"
    assert payload["project"]["geometry"]["tiles"]
    assert payload["project"]["color_counts"]
    assert "artwork" in payload["project"]
    assert "generated_artwork" in payload["project"]
    return payload


def _apply_frontend_state(current, payload):
    if payload.get("payload_kind") == "design_state":
        updates = {value["id"]: value for value in payload["tile_updates"]}
        return {
            **current,
            "stage": payload["stage"],
            "document": payload["document"],
            "project": {
                **current["project"],
                "artwork": payload["artwork"],
                "generated_artwork": payload["generated_artwork"],
                "border": payload["border"],
                "color_system": payload["color_system"],
                "color_counts": payload["color_counts"],
                "geometry": {
                    **current["project"]["geometry"],
                    "tiles": [
                        {**tile, **updates.get(tile["id"], {})}
                        for tile in current["project"]["geometry"]["tiles"]
                    ],
                },
            },
        }
    if payload.get("payload_kind") != "artwork_state":
        return payload
    return {
        **current,
        "stage": payload["stage"],
        "document": payload["document"],
        "project": {
            **current["project"],
            "artwork": payload["artwork"],
            "generated_artwork": payload["generated_artwork"],
        },
    }


def test_repeated_generate_edit_transform_regenerate_sequence_is_authoritative():
    app = _app()
    frontend = app.payload()
    source = _svg(f'<circle cx="50" cy="50" r="43" fill="{BLUE}"/>')
    status, response = _upload(app, source)
    assert status == "200 OK"
    frontend = _apply_frontend_state(frontend, response)
    transform = frontend["project"]["artwork"]["transform"]
    status, response = _generate_result(app)
    frontend = _apply_frontend_state(frontend, response)
    first = _assert_workspace_state(status, frontend)
    assert first["project"]["generated_artwork"]["revision"] == 1

    status, response = _request(
        app, "POST", "/api/designer/artwork/edit", {},
    )
    frontend = _apply_frontend_state(frontend, response)
    assert frontend["project"]["artwork"]["edit_mode"] is True
    status, response = _request(
        app, "POST", "/api/designer/artwork/transform",
        {**transform, "x_in": transform["x_in"] + 1},
    )
    frontend = _apply_frontend_state(frontend, response)
    assert frontend["project"]["generated_artwork"]["needs_regeneration"] is True
    status, response = _generate_result(app)
    frontend = _apply_frontend_state(frontend, response)
    second = _assert_workspace_state(status, frontend)
    assert second["project"]["generated_artwork"]["revision"] == 2

    _, response = _request(app, "POST", "/api/designer/artwork/edit", {})
    frontend = _apply_frontend_state(frontend, response)
    current = app.artwork.transform
    _, response = _request(
        app, "POST", "/api/designer/artwork/transform", {
            "x_in": current.x_in - current.width_in / 4,
            "y_in": current.y_in - current.height_in / 4,
            "width_in": current.width_in * 1.5,
            "height_in": current.height_in * 1.5,
        },
    )
    frontend = _apply_frontend_state(frontend, response)
    assert frontend["project"]["generated_artwork"]["needs_regeneration"] is True
    status, response = _generate_result(app)
    frontend = _apply_frontend_state(frontend, response)
    third = _assert_workspace_state(status, frontend)
    assert third["project"]["generated_artwork"]["revision"] == 3


def test_border_changes_after_generation_return_complete_current_state():
    app = _app()
    _upload(app, _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'))
    _generate(app)
    for preset in ("solid", "double", "none"):
        status, response = _request(
            app, "POST", "/api/designer/border", {"preset_id": preset},
        )
        payload = _assert_workspace_state(
            status, _apply_frontend_state(app.payload(), response)
        )
        assert payload["project"]["border"]["preset_id"] == preset
        assert payload["project"]["generated_artwork"]["needs_regeneration"] is True
        assert sum(value["count"] for value in payload["project"]["color_counts"]) == (
            payload["project"]["geometry"]["visible_piece_count"]
        )
    regenerated = _assert_workspace_state(*_generate(app))
    assert regenerated["project"]["generated_artwork"]["current"] is True


def test_mixed_mutation_sequence_reconciles_explicit_compact_artwork_state():
    app = _app()
    frontend = app.payload()
    first_svg = _svg(f'<circle cx="50" cy="50" r="40" fill="{BLUE}"/>')
    _, response = _upload(app, first_svg)
    frontend = _apply_frontend_state(frontend, response)
    transform = frontend["project"]["artwork"]["transform"]
    _, response = _request(
        app, "POST", "/api/designer/artwork/transform",
        {**transform, "y_in": transform["y_in"] + 1},
    )
    frontend = _apply_frontend_state(frontend, response)
    status, response = _request(
        app, "POST", "/api/designer/border", {"preset_id": "solid"},
    )
    frontend = _assert_workspace_state(status, _apply_frontend_state(frontend, response))
    status, response = _generate_result(app)
    frontend = _assert_workspace_state(status, _apply_frontend_state(frontend, response))
    _, response = _request(app, "POST", "/api/designer/artwork/edit", {})
    frontend = _apply_frontend_state(frontend, response)
    current = app.artwork.transform
    _, response = _request(
        app, "POST", "/api/designer/artwork/transform",
        {**current.to_dict(), "x_in": current.x_in - 1},
    )
    frontend = _apply_frontend_state(frontend, response)
    status, response = _request(
        app, "POST", "/api/designer/border", {"preset_id": "none"},
    )
    frontend = _assert_workspace_state(status, _apply_frontend_state(frontend, response))
    status, response = _generate_result(app)
    frontend = _assert_workspace_state(status, _apply_frontend_state(frontend, response))
    status, response = _request(
        app, "POST", "/api/designer/artwork/remove", {},
    )
    removed = _assert_workspace_state(status, _apply_frontend_state(frontend, response))
    assert removed["project"]["artwork"] is None
    assert removed["project"]["generated_artwork"] is None
    _, response = _upload(
        app, _svg('<rect width="100" height="100" fill="#34373d"/>'),
    )
    replacement = _apply_frontend_state(removed, response)
    assert replacement["project"]["artwork"]["source_filename"] == "mark.svg"
    assert replacement["project"]["generated_artwork"] is None


def test_frontend_has_one_canonical_serialized_mutation_pipeline():
    app = _app()
    _, script = _asset(app, "/designer.js")
    assert "let mutationQueue = Promise.resolve()" in script
    assert "mutationQueue.then(operation, operation)" in script
    assert "applyDesignerState(payload" in script
    assert 'payload?.payload_kind === "artwork_state"' in script
    assert 'payload?.payload_kind === "design_state"' in script
    assert "state.project.artwork = payload.artwork" not in script
    assert "state.project.generated_artwork = payload.generated_artwork" not in script
    for path in (
        "/api/designer/artwork/upload",
        "/api/designer/artwork/transform",
        "/api/designer/artwork/generate",
        "/api/designer/artwork/edit",
        "/api/designer/artwork/remove",
        "/api/designer/artwork/reset",
        "/api/designer/border",
    ):
        assert path in script
    assert script.count('byId("artwork-generate").addEventListener') == 1
    assert "container.replaceChildren()" in script
    assert 'button.addEventListener("click", () => chooseBorder(preset.id))' in script
    assert 'if (status) status.textContent = ""' in script
    assert "artworkInteraction.previewTransform = transform" in script
    assert "state.project.artwork.transform = transform" not in script
    assert "const proposedTransform = artworkInteraction.previewTransform" in script


def test_frontend_distinguishes_transport_failures_and_recovers_once():
    app = _app()
    _, script = _asset(app, "/designer.js")
    assert "DesignerResponseReadError" in script
    assert "DesignerResponseParseError" in script
    assert "response body read exception" in script
    assert "headers received" in script
    assert "isRecoverableSuccessfulResponse" in script
    assert '"/api/designer", undefined' in script
    assert "successful mutation response was unreadable; reconciling once" in script
    assert "authoritative state recovered" in script
    assert "authoritative recovery failed" in script
    assert "could not recover the updated state" in script
    recovery = script[
        script.index("if (isRecoverableSuccessfulResponse(error))"):
        script.index("if (state !== previousState)")
    ]
    assert '"/api/designer", undefined' in recovery
    assert "request(path, body" not in recovery


def test_compact_transform_response_avoids_retransmitting_physical_geometry():
    app = _app()
    _, upload = _upload(
        app, _svg(f'<circle cx="50" cy="50" r="40" fill="{BLUE}"/>'),
    )
    _generate(app)
    transform = app.artwork.transform.to_dict()
    status, response = _request(
        app, "POST", "/api/designer/artwork/transform",
        {**transform, "x_in": transform["x_in"] + 1},
    )
    assert status == "200 OK"
    assert response["payload_kind"] == "artwork_state"
    assert "project" not in response
    assert "geometry" not in response
    assert len(json.dumps(response).encode()) < 50_000
    assert response["generated_artwork"]["needs_regeneration"] is True


def test_failed_generation_is_followed_by_successful_mutation_without_reload():
    app = _app("alternating")
    _upload(app, _svg(
        '<defs><linearGradient id="g"><stop stop-color="red"/></linearGradient></defs>'
        '<rect width="100" height="100" fill="url(#g)"/>'
    ))
    failed_status, failed = _generate_result(app)
    assert failed_status == "400 Bad Request"
    assert "Gradient" in failed["error"]
    status, recovered = _request(
        app, "POST", "/api/designer/border", {"preset_id": "none"},
    )
    assert status == "200 OK"
    assert recovered["border"]["preset_id"] == "none"
    _request(app, "POST", "/api/designer/artwork/replace", {
        "filename": "valid.svg",
        "svg_content": _svg(f'<rect width="100" height="100" fill="{BLUE}"/>'),
    })
    status, generated = _generate_result(app)
    assert status == "200 OK"
    assert generated["generated_artwork"]["current"] is True


def _assert_compact_matches_full(frontend, full):
    compact_project = frontend["project"]
    full_project = full["project"]
    assert compact_project["geometry"] == full_project["geometry"]
    for key in ("artwork", "border", "color_system", "color_counts"):
        assert compact_project[key] == full_project[key]
    expected_generated = full_project["generated_artwork"]
    if expected_generated is not None:
        expected_generated = {
            key: value for key, value in expected_generated.items()
            if key != "assignments"
        }
    assert compact_project["generated_artwork"] == expected_generated
    assert frontend["document"] == full["document"]


def test_compact_generate_and_border_reconcile_to_authoritative_full_get():
    app = _app()
    frontend = app.payload()
    _, response = _upload(
        app, _svg(f'<circle cx="50" cy="50" r="43" fill="{BLUE}"/>'),
    )
    frontend = _apply_frontend_state(frontend, response)

    status, response = _generate_result(app)
    assert status == "200 OK"
    frontend = _apply_frontend_state(frontend, response)
    _assert_compact_matches_full(frontend, app.payload())

    status, response = _request(
        app, "POST", "/api/designer/border", {"preset_id": "double"},
    )
    assert status == "200 OK"
    frontend = _apply_frontend_state(frontend, response)
    _assert_compact_matches_full(frontend, app.payload())


def test_tile_affecting_mutations_never_return_polygon_geometry():
    app = _app()
    _, upload = _upload(
        app, _svg(f'<circle cx="50" cy="50" r="43" fill="{BLUE}"/>'),
    )
    assert "geometry" in app.payload()["project"]
    responses = [_generate_result(app)[1]]
    responses.append(_request(
        app, "POST", "/api/designer/border", {"preset_id": "solid"},
    )[1])
    responses.append(_request(
        app, "POST", "/api/designer/artwork/replace", {
            "filename": "replacement.svg",
            "svg_content": _svg('<rect width="100" height="100" fill="#34373d"/>'),
        },
    )[1])
    responses.append(_request(
        app, "POST", "/api/designer/artwork/remove", {},
    )[1])
    for response in responses:
        encoded = json.dumps(response)
        assert response["payload_kind"] == "design_state"
        assert "project" not in response
        assert "geometry" not in response
        assert "vertices_in" not in encoded
        assert all("id" in update for update in response["tile_updates"])


def test_large_canvas_generate_response_remains_smaller_than_full_state():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "panoramic"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "s"})
    _upload(app, _svg(f'<circle cx="50" cy="50" r="42" fill="{BLUE}"/>'))
    _, response = _generate_result(app)
    compact_size = len(json.dumps(response).encode())
    full_size = len(json.dumps(app.payload()).encode())
    assert compact_size < full_size / 2
    assert "vertices_in" not in json.dumps(response)


def _asset(app, path):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({
        "REQUEST_METHOD": "GET", "PATH_INFO": path,
        "CONTENT_LENGTH": "0", "wsgi.input": BytesIO(),
    }, start_response)).decode()
    return captured["status"], body

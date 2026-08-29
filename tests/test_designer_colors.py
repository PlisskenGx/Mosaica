from io import BytesIO
import json

import pytest

from mosaica.designer import DesignerProjectShell, MosaicDesignerApp
from mosaica.designer_colors import (
    CANONICAL_MOSAICA_COLORS, CURATED_MOSAICA_PALETTE,
    DEFAULT_DESIGNER_COLORS,
    DesignColor,
    DesignerColorResolution,
    MAX_DESIGN_COLORS,
    PhysicalColor,
)


def _resolution(role_mapping=None):
    return DesignerColorResolution(
        colors=(
            PhysicalColor("ivory", "#FAF9F6", "Ivory", 0),
            PhysicalColor("black", "#111111", "Black", 1),
            PhysicalColor("tan", "#A87655", "Tan", 2),
        ),
        role_to_color_id=role_mapping or {
            "background": "ivory",
            "edge": "black",
            "border_primary": "black",
            "border_secondary": "tan",
        },
    )


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
    content_type = captured["headers"]["Content-Type"]
    return captured["status"], (
        json.loads(response) if content_type.startswith("application/json")
        else response.decode()
    )


def _payload(preset="none"):
    return DesignerProjectShell.create("square-s", "m").with_border(preset).to_dict()


def test_semantic_roles_resolve_to_stable_design_color_metadata():
    resolution = _resolution()
    assert resolution.resolve("edge").color_id == "black"
    assert resolution.resolve("border_primary").color_id == "black"
    assert resolution.resolve("background").display_color == "#FAF9F6"
    payload = resolution.to_dict()
    assert [value["color_id"] for value in payload["design_colors"]] == [
        "ivory", "black", "tan",
    ]
    assert [value["order"] for value in payload["design_colors"]] == [0, 1, 2]
    assert payload["manufacturing_mapping"] is None


def test_two_semantic_roles_mapping_to_one_physical_color_merge():
    counts = _resolution().count_visible((
        ("full", "edge"),
        ("edge_cut", "border_primary"),
        ("full", "background"),
    ))
    assert [(value.color_id, value.count) for value in counts] == [
        ("ivory", 1), ("black", 2),
    ]


def test_default_background_and_edge_share_one_project_color():
    assert DEFAULT_DESIGNER_COLORS.resolve("background").color_id == (
        DEFAULT_DESIGNER_COLORS.resolve("edge").color_id
    )
    assert DEFAULT_DESIGNER_COLORS.role_to_color_id["background"] != "background"


def test_default_border_roles_are_black_and_gray_project_colors():
    primary = DEFAULT_DESIGNER_COLORS.resolve("border_primary")
    secondary = DEFAULT_DESIGNER_COLORS.resolve("border_secondary")
    assert (primary.name, primary.display_color) == ("Black", "#000000")
    assert (secondary.name, secondary.display_color) == ("Gray", "#808080")


def test_default_project_palette_has_32_stable_ordered_canonical_colors():
    values = [
        (value.color_id, value.name, value.display_color, value.order)
        for value in DEFAULT_DESIGNER_COLORS.colors
    ]
    assert len(values) == 32
    assert values[:5] == [
        ("project-color-1", "Ivory", "#FAF9F6", 0),
        ("project-color-2", "Black", "#000000", 1),
        ("project-color-3", "Gray", "#808080", 2),
        ("project-color-4", "Clay", "#B56F52", 3),
        ("project-color-5", "Denim", "#466984", 4),
    ]
    assert [value[0] for value in values] == [
        f"project-color-{index}" for index in range(1, 33)
    ]
    assert [value[3] for value in values] == list(range(32))
    assert DEFAULT_DESIGNER_COLORS.colors is CANONICAL_MOSAICA_COLORS
    assert [value[2] for value in values] == [
        display_color for _, display_color in CURATED_MOSAICA_PALETTE
    ]


def test_canonical_equivalent_colors_reuse_ids_without_append():
    assert DEFAULT_DESIGNER_COLORS.color_id_for_rgb((250, 249, 246)) == "project-color-1"
    assert DEFAULT_DESIGNER_COLORS.color_id_for_rgb((0, 0, 0)) == "project-color-2"
    assert len(DEFAULT_DESIGNER_COLORS.colors) == 32


def test_three_roles_merge_and_outside_placements_are_excluded():
    resolution = _resolution({
        "background": "black",
        "edge": "black",
        "border_primary": "black",
        "border_secondary": "tan",
    })
    counts = resolution.count_visible((
        ("full", "background"),
        ("half", "edge"),
        ("edge_cut", "border_primary"),
        ("outside", "border_primary"),
    ))
    assert [(value.color_id, value.count) for value in counts] == [("black", 3)]


def test_user_facing_counts_do_not_emit_semantic_role_names():
    counts = [value.to_dict() for value in _resolution().count_visible((
        ("full", "background"), ("half", "edge"),
    ))]
    assert counts
    assert set(counts[0]) == {
        "color_id", "display_color", "name", "count", "order",
    }
    assert all("role" not in key for value in counts for key in value)


@pytest.mark.parametrize("preset", ["none", "solid", "double", "alternating"])
def test_every_border_state_counts_all_visible_full_and_clipped_pieces(preset):
    payload = _payload(preset)
    counts = payload["color_counts"]
    geometry = payload["geometry"]
    assert sum(value["count"] for value in counts) == geometry["visible_piece_count"]
    assert geometry["visible_piece_count"] == (
        geometry["full_tile_count"] + geometry["clipped_piece_count"]
    )
    assert payload["color_system"]["design_color_safety_limit"] == 32
    assert [value["order"] for value in counts] == sorted(
        value["order"] for value in counts
    )


def test_none_counts_clipped_as_edge_and_full_as_background():
    payload = _payload("none")
    by_id = {value["color_id"]: value["count"] for value in payload["color_counts"]}
    assert DEFAULT_DESIGNER_COLORS.resolve("edge").color_id == (
        DEFAULT_DESIGNER_COLORS.resolve("background").color_id
    )
    assert by_id[DEFAULT_DESIGNER_COLORS.resolve("edge").color_id] == (
        payload["geometry"]["visible_piece_count"]
    )


def test_solid_counts_border_and_background_with_clipped_included():
    payload = _payload("solid")
    by_id = {value["color_id"]: value["count"] for value in payload["color_counts"]}
    assert by_id[DEFAULT_DESIGNER_COLORS.resolve("border_primary").color_id] == (
        payload["border"]["counts"]["border_owned"]
    )
    assert by_id[DEFAULT_DESIGNER_COLORS.resolve("background").color_id] == (
        payload["border"]["counts"]["available_artwork"]
    )


def test_double_roles_can_merge_through_alternate_physical_mapping():
    payload = _payload("double")
    resolution = _resolution({
        "background": "ivory",
        "edge": "black",
        "border_primary": "black",
        "border_secondary": "black",
    })
    counts = resolution.count_visible(
        (value["piece_type"], value["color_role"])
        for value in payload["geometry"]["tiles"]
    )
    by_id = {value.color_id: value.count for value in counts}
    assert by_id["black"] == payload["border"]["counts"]["border_owned"]
    assert by_id["ivory"] == payload["border"]["counts"]["available_artwork"]


def test_alternating_counts_are_deterministic():
    assert _payload("alternating")["color_counts"] == (
        _payload("alternating")["color_counts"]
    )


def test_switching_refreshes_counts_without_stale_values_and_keeps_order():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _, current = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    payloads = [current]
    for preset in ("solid", "double", "alternating", "none"):
        _, current = _request(
            app, "POST", "/api/designer/border", {"preset_id": preset},
        )
        payloads.append(current)
    for payload in payloads:
        project = payload.get("project", payload)
        counts = project["color_counts"]
        assert sum(value["count"] for value in counts) == (
            app.project.to_dict()["geometry"]["visible_piece_count"]
        )
        assert [value["order"] for value in counts] == sorted(
            value["order"] for value in counts
        )
    assert payloads[0]["project"]["color_counts"] == payloads[-1]["color_counts"]


def test_more_than_four_distinct_design_colors_are_supported():
    colors = tuple(
        DesignColor(
            f"project-color-{index + 1}", f"#{index:02X}0000",
            f"Color {index}", index,
        )
        for index in range(8)
    )
    resolution = DesignerColorResolution(
        colors, {"background": "project-color-1"},
    )
    assert len(resolution.colors) == 8


def _workspace_app():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    return app


def test_arbitrary_color_add_is_rejected_by_fixed_canonical_palette():
    app = _workspace_app()
    status, payload = _request(app, "POST", "/api/designer/colors/add", {
        "display_color": "rgb(22, 88, 144)", "name": "Lake",
    })
    assert status == "400 Bad Request"
    assert "32-color limit" in payload["error"]
    assert app.document_dirty is False


def test_duplicate_add_and_update_are_atomic():
    app = _workspace_app()
    before = app.payload()["project"]["color_system"]
    status, error = _request(app, "POST", "/api/designer/colors/add", {
        "display_color": "black",
    })
    assert status == "400 Bad Request"
    assert error["error"] == "This color already exists in the project."
    assert app.payload()["project"]["color_system"] == before

    status, error = _request(app, "POST", "/api/designer/colors/update", {
        "color_id": "project-color-4", "display_color": "#808080",
        "name": "Duplicate Gray",
    })
    assert status == "400 Bad Request"
    assert error["error"] == "Canonical Mosaica design colors cannot be edited."
    assert app.payload()["project"]["color_system"] == before


def test_canonical_color_update_is_rejected_atomically():
    app = _workspace_app()
    before = app.payload()["project"]
    ivory_count = next(
        value["count"] for value in before["color_counts"]
        if value["color_id"] == "project-color-1"
    )
    status, payload = _request(app, "POST", "/api/designer/colors/update", {
        "color_id": "project-color-1", "display_color": "#F2E2C4",
        "name": "Warm Ivory",
    })
    assert status == "400 Bad Request"
    assert "cannot be edited" in payload["error"]
    after = app.payload()["project"]
    assert after["color_system"] == before["color_system"]
    assert next(
        value["count"] for value in after["color_counts"]
        if value["color_id"] == "project-color-1"
    ) == ivory_count


def test_canonical_colors_cannot_be_removed():
    app = _workspace_app()
    status, error = _request(app, "POST", "/api/designer/colors/remove", {
        "color_id": "project-color-6",
    })
    assert status == "400 Bad Request"
    assert "Canonical" in error["error"]


def test_tiles_assignment_uses_direct_canonical_identity():
    app = _workspace_app()
    tile_id = app.payload()["project"]["geometry"]["tiles"][0]["id"]
    status, payload = _request(app, "POST", "/api/designer/paint", {
        "placement_ids": [tile_id], "mode": "paint",
        "color_id": "project-color-20",
    })
    assert status == "200 OK"
    assert app.paint_overrides[tile_id] == "project-color-20"
    assert payload["paint"]["overrides"][tile_id] == "project-color-20"


def test_color_limit_rejects_thirty_third_without_mutation():
    colors = tuple(
        DesignColor(
            f"project-color-{index + 1}", f"#{index:02X}0101",
            f"Color {index + 1}", index,
        )
        for index in range(MAX_DESIGN_COLORS)
    )
    resolution = DesignerColorResolution(colors, {"background": "project-color-1"})
    with pytest.raises(ValueError, match="32-color limit"):
        resolution.add_color("#FEFEFE")
    assert len(resolution.colors) == MAX_DESIGN_COLORS


def test_colors_tool_is_removed_and_tiles_owns_the_canonical_palette():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    assert 'id="colors-heading"' not in html
    assert 'id="project-colors"' not in html
    assert 'id="color-add-picker"' not in html
    assert 'id="color-error"' not in html
    assert 'id="paint-assign"' not in html
    assert 'id="tiles-heading">Tiles<' in html
    assert 'id="design-palette"' in html
    assert "/api/designer/paint/slot" not in script
    assert "/api/designer/border/color" in script
    assert script.count("function openDesignPalette") == 1
    assert script.count("state.project.paint.curated_palette") >= 1
    assert "window.alert" not in script
    assert script.count("window.confirm(") == 2


def test_api_keeps_counts_while_normal_ui_hides_them():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _, payload = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    assert payload["project"]["color_counts"]
    assert payload["project"]["geometry"]["full_tile_count"] > 0
    assert payload["project"]["geometry"]["clipped_piece_count"] > 0
    assert payload["project"]["border"]["counts"]["protected"] == 0
    assert payload["project"]["border"]["counts"]["available_artwork"] == (
        payload["project"]["geometry"]["visible_piece_count"]
    )
    _, script = _request(app, "GET", "/designer.js")
    assert "color_counts" in script
    assert "renderColorCounts" not in script
    assert 'countLabel.className = "tile-color-count"' not in script
    assert "physical-color-swatch" not in script
    assert "Visible pieces by design color" not in script
    assert "color.name" in script
    start = script.index("function renderWorkspaceStatus(project)")
    status_rendering = script[start:script.index("function refreshArtworkLayers", start)]
    color_start = script.index("function renderPaintInspector")
    color_rendering = script[color_start:script.index("function renderGroutInspector", color_start)]
    assert "querySelectorAll" not in status_rendering + color_rendering
    assert ".reduce(" not in status_rendering + color_rendering
    assert "tile.color_role" not in status_rendering + color_rendering
    assert "tileShapeLabel(project.tile_shape)" in status_rendering
    assert "orientationName" in status_rendering
    assert "project.tile_preset.title" in status_rendering
    assert "project.canvas_preset.width_in" in status_rendering
    assert "project.canvas_preset.height_in" in status_rendering
    assert "project.custom_grid.tiles_across" in status_rendering
    assert "project.custom_grid.tiles_down" in status_rendering
    assert "geometry.visible_piece_count" not in status_rendering
    assert "project.color_counts" not in status_rendering
    assert "geometry.width_in" not in status_rendering
    assert "geometry.height_in" not in status_rendering
    assert "project.tile_preset.id" not in status_rendering
    assert "project.tile_preset.flat_to_flat_mm" not in status_rendering
    assert "project.grout_mm" not in status_rendering
    assert "estimated_minimum_plates" not in status_rendering
    assert "full_tile_count" not in status_rendering
    assert "clipped_piece_count" not in status_rendering
    assert "Border:" not in status_rendering
    assert "protected" not in status_rendering
    assert "status-project-summary" in status_rendering
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert ".physical-color-counts" not in stylesheet
    assert ".physical-color-count" not in stylesheet
    assert ".physical-color-swatch" not in stylesheet
    assert ".tile-color-count" not in stylesheet
    assert ".tile-color-swatch[aria-pressed=\"true\"]" in stylesheet
    assert ".tile-color-swatch.count-light" not in stylesheet
    assert ".tile-color-swatch.count-dark" not in stylesheet
    assert ".workspace-status" in stylesheet
    assert ".status-group" in stylesheet
    assert "flex-wrap: wrap" in stylesheet
    assert "white-space: nowrap" in stylesheet


def test_toolbox_swatch_actions_and_upload_cta_use_quiet_canonical_treatment():
    app = MosaicDesignerApp()
    _, html = _request(app, "GET", "/")
    _, script = _request(app, "GET", "/designer.js")
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert 'id="paint-clear" class="text-action danger-text"' in html
    assert 'id="grout-color" class="text-action compact-color-action"' in html
    resting = stylesheet[stylesheet.index(".tile-color-swatch {"):]
    resting = resting[:resting.index("}")]
    assert "border-radius: 50%" in resting
    assert "clip-path" not in resting and "drop-shadow" not in resting
    assert "width: 2rem" in resting and "height: 2rem" in resting
    assert '.tile-color-swatch[aria-pressed="true"]' in stylesheet
    shared = stylesheet[stylesheet.index(".border-color-swatch, .artwork-color-swatch") :]
    shared = shared[:shared.index("}")]
    assert "border-radius: 50%" in shared
    assert "clip-path" not in shared
    assert ".text-action" in stylesheet
    assert 'color.color_id === "project-color-5"' in script
    assert '"--artwork-cta-color", canonicalCta.display_color' in script
    assert "#artwork-upload" in stylesheet

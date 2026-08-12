from io import BytesIO
import json

import pytest

from mosaic_engine.designer import DesignerProjectShell, MosaicDesignerApp
from mosaic_engine.designer_colors import (
    DEFAULT_DESIGNER_COLORS,
    DesignColor,
    DesignerColorResolution,
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


def test_equivalent_colors_reuse_ids_and_new_colors_append():
    updated = DEFAULT_DESIGNER_COLORS.with_artwork_colors((
        (250, 249, 246),
        (0, 0, 0),
        (0, 102, 204),
    ))
    assert len(updated.colors) == 4
    assert updated.color_id_for_rgb((250, 249, 246)) == "project-color-1"
    assert updated.color_id_for_rgb((0, 0, 0)) == "project-color-2"
    assert updated.color_id_for_rgb((0, 102, 204)) == "project-color-4"


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


def test_api_and_status_ui_render_backend_counts_without_inference():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square-s"})
    _, payload = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    assert payload["project"]["color_counts"]
    assert payload["project"]["geometry"]["full_tile_count"] > 0
    assert payload["project"]["geometry"]["clipped_piece_count"] > 0
    assert payload["project"]["border"]["counts"]["protected"] > 0
    _, script = _request(app, "GET", "/designer.js")
    assert "project.color_counts" in script
    assert "renderColorCounts" in script
    assert "physical-color-swatch" in script
    assert "Visible pieces by design color" in script
    assert "color.name" in script
    assert "color.count" in script
    assert "querySelectorAll" not in script
    assert ".reduce(" not in script
    assert "tile.color_role" not in script
    start = script.index("function renderWorkspaceStatus(project)")
    status_rendering = script[start:script.index("function refreshArtworkLayers", start)]
    assert "geometry.width_in" in status_rendering
    assert "project.tile_preset.id" in status_rendering
    assert "project.tile_preset.flat_to_flat_mm" in status_rendering
    assert "project.grout_mm" in status_rendering
    assert "geometry.visible_piece_count" in status_rendering
    assert "project.color_counts" in status_rendering
    assert "Est. ${plateEstimate.estimated_minimum_plates} plates" in status_rendering
    assert "Est. minimum" not in status_rendering
    assert "full_tile_count" not in status_rendering
    assert "clipped_piece_count" not in status_rendering
    assert "Border:" not in status_rendering
    assert "protected" not in status_rendering
    assert "status-physical-setup" in status_rendering
    assert "status-production" in status_rendering
    _, stylesheet = _request(app, "GET", "/designer.css")
    assert ".physical-color-counts" in stylesheet
    assert ".physical-color-count" in stylesheet
    assert ".physical-color-swatch" in stylesheet
    assert "flex-wrap: wrap" in stylesheet
    assert "flex: 1 1 auto" in stylesheet
    assert ".workspace-status" in stylesheet
    assert ".status-group" in stylesheet
    assert "flex-wrap: wrap" in stylesheet
    assert "white-space: nowrap" in stylesheet

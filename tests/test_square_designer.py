from dataclasses import replace
from io import BytesIO
import json
from math import isclose
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image
import pytest

from mosaica.artwork import create_artwork, update_artwork_transform
from mosaica.border import build_border_layer
from mosaica.designer import DESIGNER_GROUT_MM, DesignerProjectShell, MosaicDesignerApp
from mosaica.designer_export import DesignerExportSnapshot
from mosaica.designer_flat_export import export_flat_design, mosaic_svg
from mosaica.designer_generation import generate_designer_artwork
from mosaica.fabricate import FabricationProfile, resolve_designer_project
from mosaica.project_file import DesignerProjectFileState, load_project_file, save_project_file
from mosaica.tiles import get_tile_family, production_tile_families


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#000000"/></svg>'
PROFILE = FabricationProfile("square-rejection", 1, 1.4, 0.8)


def _request(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    captured = {}
    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)
    response = b"".join(app({
        "REQUEST_METHOD": method, "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw),
    }, start_response))
    return captured["status"], json.loads(response)


def test_designer_registry_contains_hexagon_and_square_only():
    assert [(value.id, value.display_name) for value in production_tile_families()] == [
        ("hexagon", "Hexagon"), ("square", "Square"),
    ]
    square = get_tile_family("square")
    assert [(value.id, value.display_name) for value in square.orientations()] == [
        ("straight", "Straight"),
    ]
    assert [(value.id, value.primary_dimension_mm, value.dimension_kind) for value in square.presets()] == [
        ("s", 16.0, "side_length"),
        ("m", 20.0, "side_length"),
        ("l", 24.0, "side_length"),
    ]
    assert [value.primary_dimension_mm + DESIGNER_GROUT_MM for value in square.presets()] == [
        17.8, 21.8, 25.8,
    ]


@pytest.mark.parametrize("preset", ("s", "m", "l"))
def test_square_preset_canvas_is_exact_centered_and_deterministic(preset):
    first = DesignerProjectShell.create("square", preset, "straight", family_id="square")
    second = DesignerProjectShell.create("square", preset, "straight", family_id="square")
    geometry = first.geometry
    assert geometry == second.geometry
    assert (geometry.width_in, geometry.height_in) == (24.0, 24.0)
    assert geometry.shape == "square" and geometry.orientation == "straight"
    assert all(len(value.full_vertices_in) == 4 for value in geometry.placements)
    assert any(value.piece_type == "edge_cut" for value in geometry.placements)
    left = min(x for value in geometry.placements for x, _ in value.full_vertices_in)
    right = max(x for value in geometry.placements for x, _ in value.full_vertices_in)
    top = min(y for value in geometry.placements for _, y in value.full_vertices_in)
    bottom = max(y for value in geometry.placements for _, y in value.full_vertices_in)
    assert isclose(-left, right - geometry.width_in, abs_tol=1e-10)
    assert isclose(-top, bottom - geometry.height_in, abs_tol=1e-10)
    assert min(value.piece_fraction for value in geometry.placements) > 0.19


def test_square_custom_physical_canvas_clips_without_resizing():
    shell = DesignerProjectShell.create_physical(
        "m", "straight", 24.0, 18.0, family_id="square",
    )
    assert shell.canvas_mode == "custom_physical"
    assert (shell.geometry.width_in, shell.geometry.height_in) == (24.0, 18.0)
    assert any(value.piece_type == "edge_cut" for value in shell.geometry.placements)
    assert all(
        0 <= x <= 24.0 and 0 <= y <= 18.0
        for value in shell.geometry.placements for x, y in value.vertices_in
    )


def test_square_counted_grid_can_have_clean_whole_tile_edges():
    shell = DesignerProjectShell.create_custom(
        "m", "straight", 7, 5, family_id="square",
    )
    side = 20.0 / 25.4
    grout = 1.8 / 25.4
    assert isclose(shell.geometry.width_in, 7 * side + 6 * grout)
    assert isclose(shell.geometry.height_in, 5 * side + 4 * grout)
    assert all(value.piece_type == "full" for value in shell.geometry.placements)


def test_square_topology_and_border_compatibility():
    family = get_tile_family("square")
    assert family.topology.expected_neighbor_degree == 4
    assert set(family.neighbors(1, 1, 3, 3, "straight")) == {
        (1, 0), (1, 2), (0, 1), (2, 1),
    }
    shell = DesignerProjectShell.create_physical(
        "m", "straight", 4.0, 3.0, family_id="square",
    )
    none = build_border_layer(shell.geometry, "none")
    assert not none.protected_placement_ids
    assert any(
        shell.geometry.placement(value.row, value.column).piece_type == "edge_cut"
        for value in shell.geometry.placements
        if f"placement-{value.row * shell.geometry.columns + value.column:06d}" in none.available_artwork_placement_ids
    )
    solid = build_border_layer(shell.geometry, "solid")
    assert solid.protected_placement_ids
    with pytest.raises(ValueError, match="unsupported for Square"):
        shell.with_border("double")
    with pytest.raises(ValueError, match="unsupported for Square"):
        shell.with_border("alternating")


def test_square_artwork_includes_clipped_tiles_and_manual_paint_wins():
    shell = DesignerProjectShell.create_physical(
        "l", "straight", 3.0, 2.0, family_id="square",
    )
    border = build_border_layer(shell.geometry, "none")
    artwork = create_artwork("square.svg", SVG, shell.geometry, border)
    artwork = update_artwork_transform(
        artwork, x_in=0.0, y_in=0.0,
        width_in=shell.geometry.width_in, height_in=shell.geometry.height_in,
    )
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
    with patch("mosaica.designer_generation._rasterize_svg", return_value=image):
        generated = generate_designer_artwork(
            artwork, shell.geometry, border, shell.color_system, 1,
        )
    clipped_ids = {
        f"placement-{index:06d}" for index, value in enumerate(shell.geometry.placements)
        if value.piece_type == "edge_cut"
    }
    generated_ids = {value.tile_id for value in generated.assignments}
    assert clipped_ids & generated_ids
    override_id = next(iter(generated_ids))
    payload = shell.to_dict(generated, {override_id: "project-color-2"})
    tile = next(value for value in payload["geometry"]["tiles"] if value["id"] == override_id)
    assert tile["manual_override"] == "project-color-2"
    assert tile["color_id"] == "project-color-2"


def test_square_keyboard_navigation_is_cardinal_and_stable():
    shell = DesignerProjectShell.create_custom(
        "m", "straight", 3, 3, family_id="square",
    )
    geometry = shell.to_dict()["geometry"]
    center = "placement-000004"
    assert geometry["keyboard_navigation"][center] == {
        "ArrowLeft": "placement-000003",
        "ArrowRight": "placement-000005",
        "ArrowUp": "placement-000001",
        "ArrowDown": "placement-000007",
    }


def test_square_schema_v2_round_trip_and_flat_exports(tmp_path):
    shell = DesignerProjectShell.create_physical(
        "m", "straight", 4.25, 3.1, family_id="square",
    ).with_border("solid")
    tile_id = shell.to_dict()["geometry"]["tiles"][-1]["id"]
    state = DesignerProjectFileState(
        shell, None, None, {tile_id: "project-color-2"}, True, "Square",
    )
    path = save_project_file(tmp_path / "square.mosaica", state)
    reopened = load_project_file(path)
    assert reopened.project.tile_family == "square"
    assert reopened.project.tile_orientation == "straight"
    assert reopened.project.geometry == shell.geometry
    assert reopened.paint_overrides == {tile_id: "project-color-2"}
    snapshot = DesignerExportSnapshot(
        reopened.project, reopened.generated_artwork, reopened.paint_overrides, "Square",
    )
    svg = mosaic_svg(snapshot)
    root = ET.fromstring(svg)
    assert root.attrib["data-tile-family"] == "square"
    assert root.attrib["data-tile-orientation"] == "straight"
    assert root.attrib["width"].endswith("mm")
    assert root.findall(".//{http://www.w3.org/2000/svg}polygon")
    for extension in ("png", "jpg"):
        result = export_flat_design(snapshot, tmp_path / f"square.{extension}", extension)
        assert result.path.exists() and result.width_px and result.height_px
    with pytest.raises(ValueError, match="Unsupported production fabrication family: square"):
        resolve_designer_project(reopened.project, PROFILE)


def test_canvas_first_square_setup_and_export_safety_wall():
    app = MosaicDesignerApp()
    _, started = _request(app, "POST", "/api/designer/new", {"canvas_first": True})
    assert started["stage"] == "canvas"
    _, family = _request(app, "POST", "/api/designer/canvas", {"canvas_id": "square"})
    assert family["selected_canvas_id"] == "square"
    _, sizes = _request(app, "POST", "/api/designer/shape", {
        "shape": "square", "orientation": "straight",
    })
    assert sizes["stage"] == "tile"
    assert [value["side_length_mm"] for value in sizes["tile_presets"]] == [16.0, 20.0, 24.0]
    _, workspace = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    assert workspace["stage"] == "workspace"
    assert workspace["project"]["tile_family"] == "square"
    assert [value["id"] for value in workspace["border_presets"]] == ["none", "solid"]
    status, error = _request(app, "POST", "/api/designer/export/preview", {"mode": "studio"})
    assert status == "400 Bad Request"
    assert "Unsupported production fabrication family: square" in error["error"]


def test_canvas_first_custom_physical_square_flow_preserves_exact_dimensions():
    app = MosaicDesignerApp()
    _request(app, "POST", "/api/designer/new", {"canvas_first": True})
    _, family = _request(app, "POST", "/api/designer/canvas", {
        "canvas_id": "custom", "width_in": 24.0, "height_in": 18.0,
    })
    assert family["stage"] == "shape"
    _request(app, "POST", "/api/designer/shape", {
        "shape": "square", "orientation": "straight",
    })
    _, workspace = _request(app, "POST", "/api/designer/tile", {"tile_id": "m"})
    assert workspace["stage"] == "workspace"
    assert workspace["project"]["canvas_mode"] == "custom_physical"
    assert workspace["project"]["geometry"]["width_in"] == 24.0
    assert workspace["project"]["geometry"]["height_in"] == 18.0


def test_square_setup_and_export_ui_are_family_aware():
    root = Path(__file__).parents[1] / "mosaica" / "web"
    html = (root / "designer.html").read_text()
    script = (root / "designer.js").read_text()
    assert "Choose tile family" in html and 'id="shape-square"' in html
    assert 'shape: "square", orientation: "straight"' in script
    assert 'SETUP_STAGE_ORDER = ["shape", "tile", "canvas", "custom"]' in script
    assert "side_length_mm" in script and "Square fabrication is not yet available." in script
    assert 'aria-disabled' in script and "family-unavailable" in script

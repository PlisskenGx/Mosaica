from io import BytesIO
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image
import pytest

from mosaica.designer import DesignerProjectShell, MosaicDesignerApp
from mosaica.designer_flat_export import MM_PER_INCH, export_flat_design, mosaic_svg
from mosaica.fabricate.modes import FabricationMode
from mosaica.fabricate.panelize import P1S_V1_SAFE_ENVELOPE_MM


def _request(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    route, _, query = path.partition("?")
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(app({
        "REQUEST_METHOD": method, "PATH_INFO": route, "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw),
    }, start_response))
    if captured["headers"]["Content-Type"].startswith("application/json"):
        response = json.loads(response)
    else:
        response = response.decode()
    return captured["status"], response


def _workspace(tmp_path=None, **kwargs):
    app = MosaicDesignerApp(export_root=tmp_path, **kwargs)
    app.project = DesignerProjectShell.create_custom("l", "point_top", 3, 3)
    return app


def test_fresh_launch_welcome_new_and_cancelled_open_are_non_dirty():
    app = MosaicDesignerApp(project_open_dialog=lambda: None)
    _, initial = _request(app, "GET", "/api/designer")
    assert initial["stage"] == "welcome"
    assert initial["project"] is None
    assert initial["document"]["dirty"] is False
    _, cancelled = _request(app, "POST", "/api/designer/project/open", {})
    assert cancelled["stage"] == "welcome" and cancelled["cancelled"] is True
    _, started = _request(app, "POST", "/api/designer/new", {})
    assert started["stage"] == "shape"
    assert started["document"]["dirty"] is False


def test_welcome_open_loads_valid_document_and_failure_is_atomic(tmp_path):
    source = tmp_path / "Existing.mosaica"
    creator = _workspace(project_save_dialog=lambda _current: source)
    tile_id = creator.project.to_dict()["geometry"]["tiles"][0]["id"]
    creator.paint_overrides = {tile_id: "project-color-2"}
    _request(creator, "POST", "/api/designer/project/save", {})
    app = MosaicDesignerApp(project_open_dialog=lambda: source)
    status, opened = _request(app, "POST", "/api/designer/project/open", {})
    assert status == "200 OK" and opened["stage"] == "workspace"
    assert app.paint_overrides == {tile_id: "project-color-2"}

    failed = MosaicDesignerApp(project_open_dialog=lambda: tmp_path / "missing.mosaica")
    status, _ = _request(failed, "POST", "/api/designer/project/open", {})
    assert status == "400 Bad Request"
    assert failed.project is None and failed.payload()["stage"] == "welcome"


def test_welcome_document_menu_and_export_chooser_are_accessible_assets():
    _, html = _request(MosaicDesignerApp(), "GET", "/")
    _, script = _request(MosaicDesignerApp(), "GET", "/designer.js")
    assert html.count('class="choice-card welcome-card"') == 2
    assert "New Mosaic" in html and "Open Mosaic" in html
    assert "Recent Mosaic" not in html
    assert 'aria-haspopup="menu"' in html and 'role="menu"' in html
    assert "Open…" in html and "Save As…" in html and "Export…" in html
    assert 'data-export-format="svg"' in html
    assert 'data-export-format="png"' in html
    assert 'data-export-format="jpg"' in html
    assert 'data-export-format="print_package"' in html
    assert 'data-export-format="stl"' in html
    assert 'data-export-format="step"' not in html.lower()
    assert "closeDocumentMenu" in script and "openDocumentMenu" in script
    assert '[role=menu]:not([hidden])' in script


def test_svg_uses_physical_geometry_effective_colors_and_editable_groups():
    app = _workspace()
    payload = app.project.to_dict()
    full = next(tile for tile in payload["geometry"]["tiles"] if tile["piece_type"] == "full")
    app.project = app.project.with_border("solid")
    app.paint_overrides = {full["id"]: "project-color-4"}
    root = ET.fromstring(mosaic_svg(app._export_snapshot()))
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    geometry = app.project.to_dict(None, app.paint_overrides)["geometry"]
    width_mm = geometry["width_in"] * MM_PER_INCH
    height_mm = geometry["height_in"] * MM_PER_INCH
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["width"] == f"{width_mm:.6f}".rstrip("0").rstrip(".") + "mm"
    assert root.attrib["height"] == f"{height_mm:.6f}".rstrip("0").rstrip(".") + "mm"
    assert root.attrib["viewBox"].startswith("0 0 ")
    polygons = root.findall(".//svg:polygon", namespace)
    assert len(polygons) == geometry["visible_piece_count"]
    assert any(value.attrib["data-piece-type"] != "full" for value in polygons)
    painted = root.find(f".//svg:polygon[@id='{full['id']}']", namespace)
    assert painted is not None and painted.attrib["data-manual-override"] == "project-color-4"
    assert root.find("svg:g[@id='grout']", namespace) is not None
    grout_rect = root.find("svg:g[@id='grout']/svg:rect", namespace)
    assert grout_rect is not None
    assert grout_rect.attrib["fill"] == app.project.to_dict()["grout"]["display_color"]
    border = root.find("svg:g[@id='border']", namespace)
    assert border is not None and border.attrib["data-preset"] == "solid"
    assert root.find("svg:g[@id='tile-color-4']", namespace).attrib["fill"] == "#B56F52"


@pytest.mark.parametrize("format_name,mode", (("png", "RGB"), ("jpg", "RGB")))
def test_raster_exports_share_authoritative_state_and_dimensions(tmp_path, format_name, mode):
    app = _workspace()
    tile = next(
        value for value in app.project.to_dict()["geometry"]["tiles"]
        if value["piece_type"] == "full"
    )
    app.paint_overrides = {tile["id"]: "project-color-4"}
    result = export_flat_design(
        app._export_snapshot(), tmp_path / f"Mosaic.{format_name}", format_name,
    )
    with Image.open(result.path) as image:
        assert image.format == ("JPEG" if format_name == "jpg" else "PNG")
        assert image.mode == mode
        assert image.size == (result.width_px, result.height_px)
        assert max(image.size) == 2400
        expected = app.project.geometry.width_in / app.project.geometry.height_in
        assert image.width / image.height == pytest.approx(expected, rel=0.001)
        assert image.getpixel((0, 0)) is not None
        center_x, center_y = tile["center_in"]
        sampled = image.getpixel((
            round(center_x / app.project.geometry.width_in * image.width),
            round(center_y / app.project.geometry.height_in * image.height),
        ))
        expected_rgb = (181, 111, 82)
        tolerance = 8 if format_name == "jpg" else 0
        assert all(abs(first - second) <= tolerance for first, second in zip(sampled, expected_rgb))


def test_flat_export_api_uses_dialog_current_memory_and_preserves_dirty(tmp_path):
    destinations = {
        name: tmp_path / f"Current Mosaic.{name}" for name in ("svg", "png", "jpg")
    }
    app = _workspace(export_file_dialog=lambda name, _default: destinations[name])
    tile_id = app.project.to_dict()["geometry"]["tiles"][0]["id"]
    app.paint_overrides = {tile_id: "project-color-4"}
    app.document_dirty = True
    for format_name, destination in destinations.items():
        status, result = _request(app, "POST", "/api/designer/export/file", {
            "format": format_name,
        })
        assert status == "200 OK" and Path(result["path"]) == destination
        assert destination.is_file()
        assert app.document_dirty is True


@pytest.mark.parametrize("mode", (FabricationMode.STUDIO, FabricationMode.MUSEUM))
def test_stl_package_reuses_mode_aware_fabricate_meshes(tmp_path, mode):
    app = _workspace(tmp_path)
    output = tmp_path / mode.value
    result = app.export_service.generate_stl(app._export_snapshot(), mode, output)
    manifest = json.loads(result.manifest_path.read_text())
    assert result.stl_paths
    assert all(path.is_file() for path in result.stl_paths)
    assert any("Panel_A1_Base.stl" == path.name for path in result.stl_paths)
    assert any("Panel_A1_Grout-Thinset.stl" == path.name for path in result.stl_paths)
    assert any("Panel_A1_Tile_1_Ivory.stl" == path.name for path in result.stl_paths)
    assert manifest["fabrication_mode"]["id"] == mode.value
    expected_envelope = 228.0 if mode is FabricationMode.STUDIO else P1S_V1_SAFE_ENVELOPE_MM[0]
    assert manifest["safe_panel_envelope_mm"] == {
        "width": expected_envelope, "height": expected_envelope,
    }
    assert manifest["tile_assignment"]["tile_cuts_created"] == 0
    assert result.geometry_signature == manifest["geometry_signature_sha256"]
    assert all("user_facing_name" in record for record in manifest["body_channel_ownership"])

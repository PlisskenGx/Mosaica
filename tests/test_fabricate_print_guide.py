import json

from mosaica.fabricate.panelize import build_panelized_fabrication, panelize_model
from mosaica.fabricate.phase2b import build_production_model
from mosaica.fabricate.print_guide import (
    BAMBU_CORE_WARNING,
    GUIDE_FILENAME,
    build_print_guide_content,
)
from mosaica.fabricate.three_mf import export_panelized_three_mf_package


def _export(tmp_path, mode, *, project_name="Print Guide Fixture"):
    model = build_production_model()
    before = model.to_dict()
    fabrication = build_panelized_fabrication(panelize_model(model, mode=mode))
    package = export_panelized_three_mf_package(
        fabrication,
        tmp_path / mode,
        mode=mode,
        project_name=project_name,
    )
    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    content = build_print_guide_content(fabrication.plan, manifest)
    return model, before, fabrication, package, manifest, content


def test_print_guide_is_created_from_authoritative_project_and_panel_data(tmp_path):
    model, before, fabrication, package, manifest, content = _export(
        tmp_path, "studio",
    )
    assert package.print_guide_path.name == GUIDE_FILENAME
    assert package.print_guide_path.is_file()
    pdf = package.print_guide_path.read_bytes()
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert pdf.count(b"/Type /Page ") == 3
    assert b"Print Guide Fixture" in pdf
    assert b"by Veradura Design" in pdf
    assert b"STUDIO MODE" in pdf
    assert manifest["fabrication_mode"] == {
        "id": "studio",
        "display_name": "Studio",
        "quality_tradeoff": manifest["fabrication_mode"]["quality_tradeoff"],
    }
    assert manifest["artifacts"]["print_guide"] == GUIDE_FILENAME

    assert content.project_name == "Print Guide Fixture"
    assert (content.width_mm, content.height_mm) == (
        model.artwork_width_mm, model.artwork_height_mm,
    )
    assert content.tile_flat_to_flat_mm == model.tile_flat_to_flat_mm
    assert content.tile_orientation == model.tile_orientation
    assert content.grout_gap_mm == model.grout_gap_mm
    assert len(content.panels) == len(fabrication.plan.panels)
    assert tuple(panel.panel_id for panel in content.panels) == tuple(
        panel.panel_id for panel in fabrication.plan.panels
    )
    assert all(panel.panel_id.encode() in pdf for panel in content.panels)
    assert b"FRONT VIEW" in pdf
    assert b"A1 is top-left" in pdf
    assert b"Rear inspection reverses physical left/right" in pdf
    assert model.to_dict() == before


def test_studio_guide_contains_exact_operator_actions_without_false_claims(tmp_path):
    _, _, _, package, _, content = _export(tmp_path, "studio")
    pdf = package.print_guide_path.read_bytes()
    assert content.mode_id == "studio"
    assert content.mode_instructions == (
        ("Prime Tower", "Others", "Enable", "Uncheck"),
        ("Brim", "Others", "Brim type", "Set to No Brim"),
        ("Adaptive Variable Layer Height", "Quality", "Enable", "Enable"),
    )
    for phrase in (
        b"Prime Tower: OFF",
        b"Brim: No Brim",
        b"Ironing: OFF",
        b"does not disable nozzle flushing",
        b"Studio mode",
        b"panels up to 228 x 228 mm",
        b"one object with multiple logical parts",
        b"exact exported-part mapping on page 1",
        b"Tile 1 - Ivory",
        b"Tile 2 - Black",
        b"Tile 3 - Clay",
        b"Position the panel manually",
        b"complete panel",
        BAMBU_CORE_WARNING.encode(),
    ):
        assert phrase in pdf
    assert b"automatically positioned" not in pdf
    assert b"two-part epoxy" not in pdf
    assert b"cyanoacrylate" not in pdf
    assert b"Fast mode" not in pdf
    assert content.part_mapping == (
        ("Base", "Base", "#808080"),
        ("Grout-Thinset", "Grout", "#FAF9F6"),
        ("Tile 1 - Ivory", "Ivory", "#FAF9F6"),
        ("Tile 2 - Black", "Black", "#000000"),
        ("Tile 3 - Clay", "Clay", "#B56F52"),
    )


def test_museum_guide_contains_exact_finish_actions_and_interference_check(tmp_path):
    _, _, _, package, manifest, content = _export(tmp_path, "museum")
    pdf = package.print_guide_path.read_bytes()
    assert content.mode_id == "museum"
    assert manifest["process_intent"]["ironing"] == {
        "enabled": True,
        "type": "Topmost surfaces",
        "pattern": "Concentric",
        "flow_percent": 18,
        "speed_mm_s": 30,
        "line_spacing_mm": 0.15,
    }
    for phrase in (
        b"MUSEUM MODE",
        b"Prime Tower: ON",
        b"Use the Bambu default",
        b"No brim width is prescribed",
        b"Topmost surfaces",
        b"Concentric",
        b"18% flow",
        b"30 mm/s",
        b"0.15 mm line spacing",
        b"do not interfere",
        b"Mosaica does not",
        b"position the Prime Tower",
        b"calculated Prime Tower and panel do not interfere",
    ):
        assert phrase in pdf


def test_print_guide_is_deterministic_and_preserves_three_mf_geometry(tmp_path):
    first = _export(tmp_path / "first", "studio")
    second = _export(tmp_path / "second", "studio")
    first_package = first[3]
    second_package = second[3]
    assert first_package.print_guide_path.read_bytes() == (
        second_package.print_guide_path.read_bytes()
    )
    assert first_package.geometry_signature == second_package.geometry_signature
    assert [path.read_bytes() for path in first_package.three_mf_paths] == [
        path.read_bytes() for path in second_package.three_mf_paths
    ]

import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from mosaica.designer import DesignerProjectShell
from mosaica.fabricate.panelize import (
    build_panelized_fabrication,
    panelize_model,
)
from mosaica.fabricate.phase2b import PRODUCTION_PROFILE, build_production_model
from mosaica.fabricate.modes import (
    FabricationMode, resolve_fabrication_mode,
)
from mosaica.fabricate.resolve import resolve_designer_project
from mosaica.fabricate.three_mf import (
    _part_identity,
    apply_transform,
    export_three_mf_package,
    export_panelized_three_mf_package,
    inspect_panel_3mf,
    main as three_mf_main,
    panel_plate_transform,
)
from mosaica.fabricate.model import LogicalMaterialChannel


def _single_panel_fabrication(mode="studio"):
    model = build_production_model()
    return build_panelized_fabrication(panelize_model(model, mode=mode))


def test_standard_3mf_package_is_valid_deterministic_and_round_trips(tmp_path):
    fabrication = _single_panel_fabrication()
    first = export_panelized_three_mf_package(fabrication, tmp_path / "first")
    second = export_panelized_three_mf_package(fabrication, tmp_path / "second")
    assert first.geometry_signature == second.geometry_signature
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert [path.name for path in first.three_mf_paths] == ["Mosaica_A1.3mf"]
    assert first.three_mf_paths[0].read_bytes() == second.three_mf_paths[0].read_bytes()
    inspected = inspect_panel_3mf(first.three_mf_paths[0])
    assert set(inspected["package_parts"]) == {
        "[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
        "Metadata/model_settings.config",
    }
    assert len(inspected["meshes"]) == len(fabrication.panels[0].bodies)
    assert inspected["metadata"] == {
        "Title": "Mosaica Panel A1", "Application": "Mosaica 2.0.0",
    }
    with ZipFile(first.three_mf_paths[0]) as archive:
        assert archive.testzip() is None


def test_opc_documents_use_bambu_compatible_default_namespace_serialization(tmp_path):
    package = export_panelized_three_mf_package(
        _single_panel_fabrication(), tmp_path / "export",
    )
    with ZipFile(package.three_mf_paths[0]) as archive:
        content_types = archive.read("[Content_Types].xml")
        relationships = archive.read("_rels/.rels")
        model = archive.read("3D/3dmodel.model")

    assert b"ns0:" not in content_types
    assert b"ns0:" not in relationships
    assert (
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        in content_types
    )
    assert (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        in relationships
    )
    content_root = ET.fromstring(content_types)
    relationship_root = ET.fromstring(relationships)
    assert content_root.tag == (
        "{http://schemas.openxmlformats.org/package/2006/content-types}Types"
    )
    relationship = relationship_root.find(
        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    )
    assert relationship is not None
    assert relationship.attrib == {
        "Target": "/3D/3dmodel.model",
        "Id": "rel0",
        "Type": "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
    }
    model_root = ET.fromstring(model)
    metadata_names = [
        item.attrib["name"]
        for item in model_root.findall(
            "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}metadata"
        )
    ]
    assert metadata_names == ["Title", "Application"]
    assert all(":" not in name for name in metadata_names)
    build_item = model_root.find(
        "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}build/"
        "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}item"
    )
    assert build_item is not None
    assert build_item.attrib["printable"] == "1"


def test_multipart_names_channels_and_materials_remain_separate(tmp_path):
    fabrication = _single_panel_fabrication()
    package = export_panelized_three_mf_package(fabrication, tmp_path / "export")
    inspected = inspect_panel_3mf(package.three_mf_paths[0])
    names = [value["name"] for value in inspected["meshes"].values()]
    materials = [value["name"] for value in inspected["materials"]]
    assert names == [
        "Base", "Grout-Thinset", "Tile 1 - Ivory",
        "Tile 2 - Black", "Tile 3 - Clay",
    ]
    assert materials == [
        "Base", "Grout-Thinset", "Tile Color 1", "Tile Color 2", "Tile Color 3",
    ]
    assert "Tile Color 4" not in materials
    assert len(inspected["components"][inspected["build_object_id"]]) == 5
    assert list(inspected["bambu_part_names"].values()) == names


def test_embedded_transform_preserves_geometry_but_is_not_a_placement_contract(tmp_path):
    fabrication = _single_panel_fabrication()
    panel = fabrication.plan.panels[0]
    transform = panel_plate_transform(panel.bounds_mm)
    points = [
        apply_transform(point, transform)
        for body in fabrication.panels[0].bodies
        for triangle in body.triangles for point in triangle
    ]
    source_points = [
        point for body in fabrication.panels[0].bodies
        for triangle in body.triangles for point in triangle
    ]
    assert round(max(p[0] for p in points) - min(p[0] for p in points), 9) == round(
        max(p[0] for p in source_points) - min(p[0] for p in source_points), 9,
    )
    assert round(max(p[1] for p in points) - min(p[1] for p in points), 9) == round(
        max(p[1] for p in source_points) - min(p[1] for p in source_points), 9,
    )
    rotated = panel_plate_transform(panel.bounds_mm, 90)
    assert rotated != transform
    package = export_panelized_three_mf_package(fabrication, tmp_path / "export")
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["panel_placement"]["automatic_position_guaranteed"] is False


def test_studio_manifest_records_mode_actions_without_claiming_plate_placement(tmp_path):
    package = export_panelized_three_mf_package(
        _single_panel_fabrication(), tmp_path / "export",
    )
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["fabrication_mode"]["id"] == "studio"
    assert manifest["fabrication_mode"]["display_name"] == "Studio"
    assert manifest["safe_panel_envelope_mm"] == {"width": 228.0, "height": 228.0}
    assert manifest["panel_placement"] == {
        "responsibility": "user_positions_imported_panel_in_bambu_studio",
        "automatic_position_guaranteed": False,
        "proprietary_bambu_placement_metadata_emitted": False,
    }
    assert manifest["process_intent"]["prime_tower"]["user_action"] == {
        "tab": "Others", "control": "Enable", "action": "Uncheck",
    }
    assert manifest["process_intent"]["brim"]["user_action"] == {
        "tab": "Others", "control": "Brim type", "action": "Set to No Brim",
    }


def test_studio_is_the_authoritative_default_mode():
    default = resolve_fabrication_mode()
    explicit = resolve_fabrication_mode("studio")
    assert default is explicit
    assert explicit.mode is FabricationMode.STUDIO
    assert explicit.mode_id == "studio"
    assert explicit.display_name == "Studio"
    assert explicit.safe_envelope_mm == (228.0, 228.0)
    museum = resolve_fabrication_mode("museum")
    assert museum.display_name == "Museum"
    assert museum.safe_envelope_mm == (210.0, 210.0)


def test_three_mf_cli_accepts_studio_and_rejects_fast(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "mosaica.fabricate.three_mf.MosaicProject.load", lambda _path: object(),
    )
    monkeypatch.setattr(
        "mosaica.fabricate.three_mf.resolve_mosaic_project",
        lambda _project, _profile: object(),
    )

    def fake_export(_model, output, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_directory=Path(output), three_mf_paths=(),
            manifest_path=Path(output) / "manifest.json",
            print_guide_path=Path(output) / "Mosaica_Print_Guide.pdf",
            geometry_signature="fixture",
        )

    monkeypatch.setattr(
        "mosaica.fabricate.three_mf.export_three_mf_package", fake_export,
    )
    assert three_mf_main([
        "--project", "project.json", "--out", str(tmp_path),
        "--mode", "studio",
    ]) == 0
    assert captured["mode"] == "studio"
    with pytest.raises(SystemExit):
        three_mf_main([
            "--project", "project.json", "--out", str(tmp_path),
            "--mode", "fast",
        ])


def test_studio_and_museum_process_intent_do_not_change_same_panel_geometry(tmp_path):
    studio = export_panelized_three_mf_package(
        _single_panel_fabrication("studio"), tmp_path / "studio",
    )
    museum = export_panelized_three_mf_package(
        _single_panel_fabrication("museum"), tmp_path / "museum",
    )
    studio_manifest = json.loads(studio.manifest_path.read_text())
    museum_manifest = json.loads(museum.manifest_path.read_text())
    assert studio_manifest["process_intent"]["ironing"] == {"enabled": False}
    assert museum_manifest["process_intent"]["ironing"] == {
        "enabled": True, "type": "Topmost surfaces", "pattern": "Concentric",
        "flow_percent": 18, "speed_mm_s": 30, "line_spacing_mm": 0.15,
    }
    assert museum_manifest["process_intent"]["brim"] == {
        "recommendation": "Bambu default",
    }
    assert "width_mm" not in museum_manifest["process_intent"]["brim"]
    studio_meshes = inspect_panel_3mf(studio.three_mf_paths[0])["meshes"]
    museum_meshes = inspect_panel_3mf(museum.three_mf_paths[0])["meshes"]
    assert [value["triangles"] for value in studio_meshes.values()] == [
        value["triangles"] for value in museum_meshes.values()
    ]


def test_adaptive_layer_height_is_intent_not_false_embedded_metadata(tmp_path):
    package = export_panelized_three_mf_package(
        _single_panel_fabrication(), tmp_path / "export",
    )
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["process_intent"]["adaptive_variable_layer_height"] == {
        "enabled": True, "embedded_in_3mf": False,
    }
    assert manifest["architecture"]["bambu_specific_metadata_emitted"] is True
    assert manifest["architecture"]["bambu_metadata_scope"] == "part_names_only"
    assert manifest["architecture"]["bambu_metadata_files"] == [
        "Metadata/model_settings.config",
    ]


def test_manifest_part_mapping_matches_core_and_bambu_names(tmp_path):
    package = export_panelized_three_mf_package(
        _single_panel_fabrication(), tmp_path / "export",
    )
    manifest = json.loads(package.manifest_path.read_text())
    inspected = inspect_panel_3mf(package.three_mf_paths[0])
    mapping = manifest["part_mapping"]
    assert [item["user_facing_name"] for item in mapping] == [
        "Base", "Grout-Thinset", "Tile 1 - Ivory",
        "Tile 2 - Black", "Tile 3 - Clay",
    ]
    assert [item["project_color_name"] for item in mapping[2:]] == [
        "Ivory", "Black", "Clay",
    ]
    assert [item["project_color_value"] for item in mapping[2:]] == [
        "#FAF9F6", "#000000", "#B56F52",
    ]
    assert [item["user_facing_name"] for item in mapping] == [
        item["name"] for item in inspected["meshes"].values()
    ]
    assert manifest["schema"]["version"] == 3
    assert all("Bambu" not in item["user_facing_name"] for item in mapping)


def test_tile_part_identity_falls_back_to_authoritative_hex_value():
    identity = _part_identity(LogicalMaterialChannel(
        "tile-color-4", "Tile Color 4", "tile_color",
        "#12ab34", "project-color-17", 16, None,
    ))
    assert identity == {
        "part_role": "tile_color",
        "user_facing_name": "Tile 4 - #12AB34",
        "project_palette_index": 16,
        "project_color_name": None,
        "project_color_value": "#12AB34",
    }


def test_panel_identity_marking_zero_cuts_and_no_connectors_survive_package(tmp_path):
    package = export_panelized_three_mf_package(
        _single_panel_fabrication(), tmp_path / "export",
    )
    manifest = json.loads(package.manifest_path.read_text())
    panel = manifest["panels"][0]
    assert panel["panel_id"] == panel["plate_id"] == "A1"
    assert panel["filename"] == "Mosaica_A1.3mf"
    assert panel["backside_marking"] == {
        "content": "A1", "cell_size_mm": 1.0, "deboss_depth_mm": 0.35,
    }
    assert panel["validation"]["geometry_round_trip"] is True
    assert manifest["panelization"]["tile_cuts_created"] == 0
    assert manifest["panelization"]["dedicated_connector_geometry"] is False


def test_24_inch_project_exports_one_stable_3mf_per_panel(tmp_path):
    model = resolve_designer_project(
        DesignerProjectShell.create("square", "l"), PRODUCTION_PROFILE,
    )
    fabrication = build_panelized_fabrication(panelize_model(model, mode="studio"))
    package = export_panelized_three_mf_package(fabrication, tmp_path / "export")
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["panelization"] == {
        "theoretical_rows": 3, "theoretical_columns": 3,
        "final_rows": 3, "final_columns": 3, "panel_count": 9,
        "tile_cuts_created": 0, "dedicated_connector_geometry": False,
    }
    assert [path.name for path in package.three_mf_paths] == [
        f"Mosaica_{row}{column}.3mf"
        for row in "ABC" for column in range(1, 4)
    ]
    assert all(panel["validation"]["geometry_round_trip"] for panel in manifest["panels"])
    assert all(panel["logical_channel_count"] >= 3 for panel in manifest["panels"])


def test_legacy_surface_finish_maps_to_mode_before_panelization(tmp_path):
    with pytest.warns(DeprecationWarning):
        package = export_three_mf_package(
            build_production_model(), tmp_path / "legacy", surface_finish="ironed",
        )
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["fabrication_mode"]["id"] == "museum"
    assert manifest["safe_panel_envelope_mm"] == {"width": 210.0, "height": 210.0}

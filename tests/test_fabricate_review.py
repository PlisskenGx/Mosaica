import json

from mosaic_engine.fabricate import (
    FabricationProfile,
    build_single_panel_geometry,
    resolve_mosaic_project,
)
from mosaic_engine.fabricate.review import (
    REVIEW_PROFILE,
    build_review_model,
    build_review_panel,
    export_review_package,
    generate_review_package,
    main,
    validate_shared_reference_frame,
)
from mosaic_engine.geometry import build_panel_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject


def _project(tmp_path):
    config = MosaicConfig(
        tile_shape="hex", hex_orientation="pointy",
        tile_width_in=20 / 25.4, tile_height_in=20 / 25.4,
        grout_width_in=1.8 / 25.4,
        target_width_in=2.0, target_height_in=1.6,
    )
    geometry = build_panel_geometry(config, 2.0, 1.6)
    grid = [
        [(row + column) % 2 for column in range(geometry.columns)]
        for row in range(geometry.rows)
    ]
    return MosaicProject.from_result(MosaicResult(
        geometry.columns, geometry.rows, grid,
        (PaletteColor("Black", (0, 0, 0)), PaletteColor("Ivory", (250, 249, 246))),
        tmp_path / "source.svg", geometry.width_in, geometry.height_in,
        config, geometry,
    ))


def test_review_model_is_deterministic_recognizable_and_clipped():
    first = build_review_model()
    second = build_review_model()
    assert first == second
    assert first.tile_preset_id == "s"
    assert first.tile_orientation == "point_top"
    assert first.grout_gap_mm == 1.8
    assert len([value for value in first.channels if value.kind == "tile_color"]) == 3
    assert len(first.tiles) == 45
    assert sum(value.piece_type == "full" for value in first.tiles) == 27
    assert sum(value.piece_type != "full" for value in first.tiles) == 18


def test_review_profile_keeps_lower_stack_explicit_and_provisional_in_manifest(tmp_path):
    package = generate_review_package(tmp_path / "review")
    manifest = json.loads(package.manifest_path.read_text())
    profile = manifest["fabricate_profile"]
    assert REVIEW_PROFILE.base_thickness_mm == 2.0
    assert REVIEW_PROFILE.grout_thickness_mm == 1.0
    assert profile["base_thickness_mm"] == 2.0
    assert profile["base_thickness_status"] == "fixture_only"
    assert profile["grout_thinset_thickness_mm"] == 1.0
    assert profile["grout_thinset_thickness_status"] == "fixture_only"
    assert profile["straight_tile_relief_mm"] == 1.6
    assert profile["rounded_crown_mm"] == 0.8
    assert profile["total_tile_relief_above_grout_mm"] == 2.4
    assert manifest["total_z_height_mm"] == 5.4


def test_review_exports_only_used_aligned_logical_bodies(tmp_path):
    panel = build_review_panel()
    package = export_review_package(panel, tmp_path / "review")
    assert {value.name for value in package.stl_paths} == {
        "Review_Base.stl", "Review_GroutThinset.stl",
        "Review_TileColor1.stl", "Review_TileColor2.stl",
        "Review_TileColor3.stl",
    }
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["shared_reference_frame"]["valid"] is True
    assert manifest["shared_reference_frame"]["origin_mm"] == [0.0, 0.0, 0.0]
    assert manifest["fabricated_width_mm"] == manifest["artwork_width_mm"]
    assert manifest["fabricated_height_mm"] == manifest["artwork_height_mm"]
    assert validate_shared_reference_frame(panel)["errors"] == []


def test_review_manifest_has_complete_body_validation_and_hashes(tmp_path):
    package = generate_review_package(tmp_path / "review")
    manifest = json.loads(package.manifest_path.read_text())
    assert manifest["geometry_signature_sha256"] == package.geometry_signature
    assert len(package.geometry_signature) == 64
    for body in manifest["bodies"]:
        validation = body["mesh_validation"]
        assert validation["watertight"] is True
        assert validation["nonmanifold_edges"] == 0
        assert validation["degenerate_faces"] == 0
        assert validation["normals_consistent"] is True
        assert validation["vertex_count"] > 0
        assert validation["face_count"] > 0
        assert len(body["geometry_signature_sha256"]) == 64
        assert len(body["stl_sha256"]) == 64
        assert len(body["bounds_mm"]) == 6
        assert body["stl_round_trip"]["valid"] is True
        if body["logical_channel"].startswith("tile-color-"):
            spatial = body["spatial_validation"]
            assert spatial["valid"] is True
            assert spatial["connected_component_count"] == len(body["tile_ids"])
            assert spatial["cross_tile_triangles"] == 0


def test_review_regeneration_is_byte_stable(tmp_path):
    first = generate_review_package(tmp_path / "first")
    second = generate_review_package(tmp_path / "second")
    assert first.geometry_signature == second.geometry_signature
    first_files = {value.name: value.read_bytes() for value in first.stl_paths}
    second_files = {value.name: value.read_bytes() for value in second.stl_paths}
    assert first_files == second_files
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()


def test_review_export_does_not_mutate_source_mosaic_project(tmp_path):
    project = _project(tmp_path)
    before = (project.generated_grid, project.overrides, project.effective_grid)
    profile = FabricationProfile("review-mutation-test", 1, 2.0, 1.0)
    panel = build_single_panel_geometry(resolve_mosaic_project(project, profile))
    export_review_package(panel, tmp_path / "review")
    assert (project.generated_grid, project.overrides, project.effective_grid) == before


def test_review_manifest_records_application_and_fabrication_contract(tmp_path):
    manifest = json.loads(generate_review_package(tmp_path / "review").manifest_path.read_text())
    assert manifest["application_version"] == "1.9.5"
    assert manifest["units"] == "mm"
    assert manifest["coordinate_system"]["origin"] == "artwork-top-left"
    assert manifest["print_orientation"] == "backside-on-build-plate; artwork-face-up"
    assert manifest["frame_land_generated"] is False
    assert manifest["panelization"] == "not_implemented"


def test_review_module_development_entry_point(tmp_path, capsys):
    output = tmp_path / "cli-review"
    assert main(["--out", str(output)]) == 0
    assert (output / "manifest.json").exists()
    assert "Fabricate Phase 1.1 review:" in capsys.readouterr().out

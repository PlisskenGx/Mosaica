import json
from math import isclose

import pytest

from mosaic_engine.designer import DesignerProjectShell
from mosaic_engine.fabricate import (
    FabricationProfile,
    build_single_panel_geometry,
    export_single_panel_prototype,
    mesh_validation,
    resolve_designer_project,
    resolve_mosaic_project,
)
from mosaic_engine.fabricate.mesh import triangle_normal
from mosaic_engine.geometry import build_panel_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject


FIXTURE_PROFILE = FabricationProfile(
    profile_id="phase1-test-fixture",
    version=1,
    base_thickness_mm=2.0,
    grout_thickness_mm=1.0,
    crown_segments=4,
)


def _mosaic_project(tmp_path):
    grout_in = 1.8 / 25.4
    config = MosaicConfig(
        tile_shape="hex", hex_orientation="pointy",
        tile_width_in=20.0 / 25.4, tile_height_in=20.0 / 25.4,
        grout_width_in=grout_in,
        target_width_in=2.0, target_height_in=1.6,
    )
    geometry = build_panel_geometry(config, 2.0, 1.6)
    grid = [
        [(row + column) % 2 for column in range(geometry.columns)]
        for row in range(geometry.rows)
    ]
    result = MosaicResult(
        geometry.columns, geometry.rows, grid,
        (PaletteColor("Black", (0, 0, 0)), PaletteColor("Ivory", (250, 249, 246))),
        tmp_path / "source.svg", geometry.width_in, geometry.height_in,
        config, geometry,
    )
    return MosaicProject.from_result(result)


def test_mosaic_project_resolves_effective_physical_truth(tmp_path):
    project = _mosaic_project(tmp_path)
    editable = next(value for value in project.geometry.placements if value.piece_type == "full")
    project.set_override(editable.row, editable.column, 1)
    snapshot = resolve_mosaic_project(project, FIXTURE_PROFILE)
    tile = next(value for value in snapshot.tiles if value.row == editable.row and value.column == editable.column)

    assert tile.source_color_id == "palette-1"
    assert tile.material_channel_id in {"tile-color-1", "tile-color-2"}
    assert snapshot.grout_gap_mm == 1.8
    assert snapshot.units == "mm"
    assert snapshot.origin == "artwork-top-left"
    assert snapshot.print_orientation == "backside-on-build-plate; artwork-face-up"
    assert snapshot.profile.total_tile_relief_mm == 2.4
    assert snapshot.to_dict() == resolve_mosaic_project(project, FIXTURE_PROFILE).to_dict()


def test_profile_requires_missing_product_stack_dimensions():
    with pytest.raises(TypeError):
        FabricationProfile(profile_id="incomplete", version=1)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="Base thickness"):
        FabricationProfile("invalid", 1, 0.0, 1.0)


def test_authoritative_grout_is_enforced(tmp_path):
    project = _mosaic_project(tmp_path)
    project._generated_result.config = MosaicConfig(  # isolated invalid input fixture
        tile_shape="hex", grout_width_in=0.1,
    )
    with pytest.raises(ValueError, match="1.8 mm"):
        resolve_mosaic_project(project, FIXTURE_PROFILE)


def test_designer_resolution_preserves_global_channels_and_border_semantics():
    shell = DesignerProjectShell.create_custom("m", "point_top", 3, 3).with_border("solid")
    snapshot = resolve_designer_project(shell, FIXTURE_PROFILE)

    assert snapshot.tile_preset_id == "m"
    assert snapshot.tile_flat_to_flat_mm == 24.0
    assert snapshot.border_preset_id == "solid"
    assert any(value.border_owned and value.border_role == "border_primary" for value in snapshot.tiles)
    assert [value.channel_id for value in snapshot.channels[:2]] == ["base", "grout-thinset"]
    assert all(value.channel_id.startswith("tile-color-") for value in snapshot.channels[2:])
    assert len(snapshot.channels[2:]) <= 4


def test_single_panel_has_aligned_separate_watertight_bodies(tmp_path):
    snapshot = resolve_mosaic_project(_mosaic_project(tmp_path), FIXTURE_PROFILE)
    panel = build_single_panel_geometry(snapshot)

    assert panel.panel_id == "A1"
    assert [value.material_channel_id for value in panel.bodies[:2]] == ["base", "grout-thinset"]
    assert len(panel.bodies) == 2 + len([value for value in snapshot.channels if value.kind == "tile_color"])
    assert panel.body("base").bounds_mm == (
        0.0, 0.0, 0.0,
        snapshot.artwork_width_mm, snapshot.artwork_height_mm, 2.0,
    )
    assert panel.body("grout-thinset").bounds_mm == (
        0.0, 0.0, 2.0,
        snapshot.artwork_width_mm, snapshot.artwork_height_mm, 3.0,
    )
    assert all(mesh_validation(value)["watertight"] for value in panel.bodies)
    assert all(mesh_validation(value)["degenerate_faces"] == 0 for value in panel.bodies)
    assert max(value.bounds_mm[5] for value in panel.bodies) == 5.4


def test_tiles_are_face_up_with_straight_and_rounded_relief(tmp_path):
    panel = build_single_panel_geometry(resolve_mosaic_project(_mosaic_project(tmp_path), FIXTURE_PROFILE))
    tile_body = next(value for value in panel.bodies if value.material_channel_id.startswith("tile-color-"))
    z_values = {round(point[2], 9) for triangle in tile_body.triangles for point in triangle}
    assert 3.0 in z_values
    assert 4.6 in z_values
    assert 5.4 in z_values
    assert len([value for value in z_values if 4.6 < value < 5.4]) == FIXTURE_PROFILE.crown_segments - 1


def test_backside_is_flat_on_build_plate_with_outward_normals(tmp_path):
    panel = build_single_panel_geometry(resolve_mosaic_project(_mosaic_project(tmp_path), FIXTURE_PROFILE))
    base = panel.body("base")
    bottom_faces = [
        value for value in base.triangles
        if all(point[2] == 0.0 for point in value)
    ]
    assert bottom_faces
    assert all(triangle_normal(value)[2] < 0 for value in bottom_faces)


def test_flat_top_designer_orientation_reaches_fabrication():
    shell = DesignerProjectShell.create_custom("s", "flat_top", 3, 3)
    snapshot = resolve_designer_project(shell, FIXTURE_PROFILE)
    assert snapshot.tile_orientation == "flat_top"
    assert build_single_panel_geometry(snapshot).body("base").bounds_mm[0:3] == (0.0, 0.0, 0.0)


def test_clipped_tiles_use_visible_not_full_polygon(tmp_path):
    snapshot = resolve_mosaic_project(_mosaic_project(tmp_path), FIXTURE_PROFILE)
    clipped = next(value for value in snapshot.tiles if value.piece_type != "full")
    assert clipped.polygon_mm != clipped.full_polygon_mm
    assert min(x for x, _ in clipped.polygon_mm) >= 0.0
    assert min(y for _, y in clipped.polygon_mm) >= 0.0
    panel = build_single_panel_geometry(snapshot)
    body = panel.body(clipped.material_channel_id)
    points = [point for triangle in body.triangles for point in triangle]
    assert min(value[0] for value in points) >= 0.0
    assert min(value[1] for value in points) >= 0.0


def test_exported_bodies_share_origin_and_manifest_is_debuggable(tmp_path):
    snapshot = resolve_mosaic_project(_mosaic_project(tmp_path), FIXTURE_PROFILE)
    panel = build_single_panel_geometry(snapshot)
    paths = export_single_panel_prototype(panel, tmp_path / "prototype")
    manifest = json.loads(paths["manifest"].read_text())

    assert manifest["resolved_model"]["units"] == "mm"
    assert manifest["resolved_model"]["profile"]["profile_id"] == "phase1-test-fixture"
    assert {value["material_channel_id"] for value in manifest["bodies"]} == {
        value.material_channel_id for value in panel.bodies
    }
    assert all(path.read_text().startswith("solid ") for key, path in paths.items() if key != "manifest")


def test_resolution_and_geometry_do_not_mutate_project(tmp_path):
    project = _mosaic_project(tmp_path)
    before = (project.generated_grid, project.overrides, project.effective_grid)
    first = build_single_panel_geometry(resolve_mosaic_project(project, FIXTURE_PROFILE))
    second = build_single_panel_geometry(resolve_mosaic_project(project, FIXTURE_PROFILE))
    assert first == second
    assert (project.generated_grid, project.overrides, project.effective_grid) == before


def test_core_fabricate_module_has_no_designer_or_browser_runtime_dependency():
    source = __import__("pathlib").Path("mosaic_engine/fabricate/resolve.py").read_text()
    runtime_source = source.split("if TYPE_CHECKING:", 1)[0]
    assert "from ..designer import" not in runtime_source
    assert "web" not in runtime_source

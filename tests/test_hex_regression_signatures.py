"""T1 deterministic locks for validated Hex Designer and Fabricate output.

Goldens never update during tests. An intentional physical change requires a
developer to run ``build_records`` explicitly outside pytest, inspect the
semantic summaries and hashes, then deliberately replace the v1 fixture.

The known wrong-side front-view panel-numbering behavior is excluded. The A1
dot-matrix glyph geometry is locked without asserting its physical side.
Archimedean Chords is also excluded; it is a rejected surface recommendation.
"""
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image, ImageDraw
import pytest

from mosaica.artwork import create_artwork, update_artwork_transform
from mosaica.border import BORDER_PRESETS, build_border_layer
from mosaica.designer import DesignerProjectShell, tile_keyboard_navigation
from mosaica.designer_export import DesignerExportSnapshot
from mosaica.designer_flat_export import export_flat_design, mosaic_svg
from mosaica.designer_generation import generate_designer_artwork
from mosaica.fabricate.export import parse_ascii_stl, write_mesh_stl
from mosaica.fabricate.mesh import (
    build_single_panel_geometry, concave_grout_spatial_validation,
    fabrication_perimeter_bounds, mesh_validation, rounded_tile_mesh,
    rounded_tile_rings,
)
from mosaica.fabricate.panelize import (
    _shared_seams, build_panelized_fabrication, panelize_model,
)
from mosaica.fabricate.phase2b import (
    PANEL_ID_CELL_MM, PANEL_ID_DEBOSS_DEPTH_MM, PRODUCTION_PROFILE,
    PanelIdentity, _marking_cells_at, build_production_model,
)
from mosaica.fabricate.print_guide import BAMBU_CORE_WARNING, build_print_guide_content
from mosaica.fabricate.resolve import resolve_designer_project
from mosaica.fabricate.review import REVIEW_PROFILE, build_review_model
from mosaica.fabricate.three_mf import (
    BAMBU_MODEL_SETTINGS, export_panelized_three_mf_package, inspect_panel_3mf,
)
from mosaica.project_file import DesignerProjectFileState, load_project_file, save_project_file
from tests.helpers.hex_regression import canonicalize, geometry_record, mesh_record, plan_record, signature

GOLDEN = Path(__file__).parent / "fixtures" / "hex_regression_v1.json"
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
       '<rect width="50" height="100" fill="#000000"/>'
       '<rect x="50" width="50" height="100" fill="#B56F52"/></svg>')

def summarized(record):
    placements = record["placements"]
    return {
        "bounds_in": canonicalize(record["bounds_in"]), "grid": record["grid"],
        "placements": len(placements),
        "visible": sum(x["piece_type"] != "outside" for x in placements),
        "full": sum(x["piece_type"] == "full" for x in placements),
        "clipped": sum(x["piece_type"] not in {"full", "outside"} for x in placements),
    }

def generated_fixture():
    shell = DesignerProjectShell.create_custom("m", "point_top", 5, 5).with_border("solid")
    border = build_border_layer(shell.geometry, "solid")
    artwork = create_artwork("embedded-lock.svg", SVG, shell.geometry, border)
    artwork = update_artwork_transform(
        artwork, x_in=.25, y_in=.2,
        width_in=shell.geometry.width_in-.5, height_in=shell.geometry.height_in-.4,
    )
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 255))
    ImageDraw.Draw(image).rectangle((64, 0, 127, 127), fill=(181, 111, 82, 255))
    with patch("mosaica.designer_generation._rasterize_svg", return_value=image):
        generated = generate_designer_artwork(
            artwork, shell.geometry, border, shell.color_system, 7,
        )
    return shell, artwork, generated

def clipped_record(shell):
    geometry = shell.geometry
    visible = [(i, x) for i, x in enumerate(geometry.placements) if x.piece_type != "outside"]
    choices = {
        "full": next(x for x in visible if x[1].piece_type == "full"),
        "nearest_half": min(visible, key=lambda x: abs(x[1].piece_fraction-.5)),
        "smallest": min(visible, key=lambda x: x[1].piece_fraction),
    }
    return {name: {
        "id": f"placement-{index:06d}", "row": tile.row, "column": tile.column,
        "center": [tile.center_x_in, tile.center_y_in],
        "piece_type": tile.piece_type, "fraction": tile.piece_fraction,
        "full": tile.full_vertices_in, "visible": tile.vertices_in,
    } for name, (index, tile) in choices.items()}

def build_records(root: Path):
    records = {"fixture": {
        "id": "mosaica-hex-regression-v1", "float_digits": 9,
        "known_exclusions": ["front-view-panel-order", "archimedean-chords"],
    }}
    cases = {
        "preset_point": DesignerProjectShell.create("square", "m", "point_top"),
        "preset_flat": DesignerProjectShell.create("landscape", "m", "flat_top"),
    }
    for orientation in ("point_top", "flat_top"):
        for across, down in ((5,5), (4,5), (5,4), (4,4)):
            cases[f"custom_{orientation}_{across}x{down}"] = DesignerProjectShell.create_custom(
                "l", orientation, across, down,
            )
    records["geometry"] = {}
    for name, shell in cases.items():
        value = geometry_record(shell)
        records["geometry"][name] = {"summary": summarized(value), "sha256": signature(value)}
    records["clipped"] = {
        orientation: {"semantic": canonicalize(value), "sha256": signature(value)}
        for orientation in ("point_top", "flat_top")
        for value in [clipped_record(DesignerProjectShell.create_custom("l", orientation, 5, 4))]
    }
    records["borders"] = {}
    for orientation in ("point_top", "flat_top"):
        shell = DesignerProjectShell.create_custom("m", orientation, 5, 5)
        for preset in BORDER_PRESETS:
            layer = build_border_layer(shell.geometry, preset.id)
            value = layer.to_dict()
            records["borders"][f"{orientation}_{preset.id}"] = {
                "owned": len(layer.border_owned_placement_ids),
                "available": len(layer.available_artwork_placement_ids),
                "rings": [len(x) for x in layer.layer_placement_ids],
                "sha256": signature(value),
            }
    shell, artwork, generated = generated_fixture()
    art_record = {
        "transform": artwork.transform.to_dict(),
        "assignments": [x.to_dict() for x in generated.assignments],
        "source_colors": generated.source_colors, "remaps": generated.color_remaps,
        "protected": build_border_layer(shell.geometry, "solid").protected_placement_ids,
    }
    records["artwork"] = {"assignments": len(generated.assignments), "sha256": signature(art_record)}
    generated_tile = generated.assignments[0].tile_id
    border_tile = build_border_layer(shell.geometry, "solid").border_owned_placement_ids[0]
    overrides = {generated_tile: "project-color-5", border_tile: "project-color-4"}
    effective = shell.to_dict(generated, overrides)
    cleared = shell.to_dict(generated, {border_tile: "project-color-4"})
    effective_record = {
        "overrides": overrides,
        "effective": {x["id"]: x["color_id"] for x in effective["geometry"]["tiles"]},
        "lower": {x["id"]: x["lower_color_id"] for x in effective["geometry"]["tiles"]},
        "restored": next(x["color_id"] for x in cleared["geometry"]["tiles"] if x["id"] == generated_tile),
    }
    records["effective_colors"] = {"count": 2, "sha256": signature(effective_record)}
    records["keyboard"] = {}
    for orientation in ("point_top", "flat_top"):
        geometry = DesignerProjectShell.create_custom("m", orientation, 5, 5).to_dict()["geometry"]
        nav, center = tile_keyboard_navigation(geometry["tiles"])
        edge = min(geometry["tiles"], key=lambda x: (x["center_in"][0], x["center_in"][1]))
        clipped = next(x for x in geometry["tiles"] if x["piece_type"] != "full")
        value = {"center_id": center, "center": nav[center], "edge_id": edge["id"],
                 "edge": nav[edge["id"]], "clipped_id": clipped["id"],
                 "clipped": nav[clipped["id"]], "map": nav}
        records["keyboard"][orientation] = {
            "samples": canonicalize({k:v for k,v in value.items() if k != "map"}),
            "sha256": signature(value),
        }
    state = DesignerProjectFileState(shell, artwork, generated, overrides, True, "Hex Lock")
    project_path = save_project_file(root / "hex-lock.mosaica", state)
    with ZipFile(project_path) as archive:
        project_json = json.loads(archive.read("project.json"))
        asset = project_json["project"]["artwork"]["embedded_path"]
        archive_record = {"members": archive.namelist(), "project": project_json,
                          "asset_sha256": sha256(archive.read(asset)).hexdigest()}
    loaded = load_project_file(project_path)
    before = resolve_designer_project(shell, PRODUCTION_PROFILE,
                                      generated_artwork=generated, paint_overrides=overrides)
    after = resolve_designer_project(loaded.project, PRODUCTION_PROFILE,
                                     generated_artwork=loaded.generated_artwork,
                                     paint_overrides=loaded.paint_overrides)
    records["project_schema_v1"] = {
        "schema": project_json["schema_version"],
        "application": project_json["application_version"],
        "semantic_sha256": signature(archive_record),
        "round_trip_equal": before == after,
        "resolved_sha256": signature(after.to_dict()),
    }
    models = {
        orientation: resolve_designer_project(
            DesignerProjectShell.create_custom("m", orientation, 5, 5),
            PRODUCTION_PROFILE, paint_overrides={"placement-000016":"project-color-4"},
        ) for orientation in ("point_top", "flat_top")
    }
    records["resolved_models"], records["perimeters"] = {}, {}
    for orientation, model in models.items():
        records["resolved_models"][orientation] = {
            "tiles": len(model.tiles), "channels": len(model.channels),
            "sha256": signature(model.to_dict()),
        }
        bounds = fabrication_perimeter_bounds(model)
        records["perimeters"][orientation] = canonicalize({
            "designer": model.artwork_bounds_mm, "fabrication": bounds,
            "size": [bounds[2]-bounds[0], bounds[3]-bounds[1]],
            "half_grout_correction": model.grout_gap_mm/2,
        })
    review = build_review_model()
    grout_top = review.profile.base_thickness_mm + review.profile.grout_thickness_mm
    records["crown"] = {"profile": canonicalize(asdict(review.profile))}
    review_panel = build_single_panel_geometry(review)
    for label, tile in (("full", next(x for x in review.tiles if x.piece_type == "full")),
                        ("clipped", next(x for x in review.tiles if x.piece_type != "full"))):
        rings = rounded_tile_rings(tile.full_polygon_mm, grout_top,
                                   review.profile.straight_tile_relief_mm,
                                   review.profile.rounded_crown_mm,
                                   review.profile.crown_segments)
        mesh = rounded_tile_mesh(tile.full_polygon_mm, grout_top,
                                 review.profile.straight_tile_relief_mm,
                                 review.profile.rounded_crown_mm,
                                 review.profile.crown_segments)
        records["crown"][label] = {
            "tile": tile.tile_id, "piece_type": tile.piece_type,
            "parent_polygon": canonicalize(tile.full_polygon_mm),
            "visible_polygon": canonicalize(tile.polygon_mm),
            "ring_count": len(rings), "vertices_per_ring": [len(x) for x in rings],
            "triangle_count": len(mesh), "rings_sha256": signature(rings),
            "mesh_sha256": signature(mesh),
            "grout_top_z_mm": grout_top,
            "finished_z_mm": grout_top + review.profile.total_tile_relief_mm,
        }
        body = review_panel.body(tile.material_channel_id)
        tile_index = body.tile_ids.index(tile.tile_id)
        start = sum(body.solid_triangle_counts[:tile_index])
        count = body.solid_triangle_counts[tile_index]
        final_triangles = body.triangles[start:start + count]
        records["crown"][label]["final_triangle_count"] = count
        records["crown"][label]["final_mesh_sha256"] = signature(final_triangles)
    production = build_production_model()
    production_panel = build_single_panel_geometry(production)
    grout = production_panel.body("grout-thinset")
    flat = replace(review, profile=REVIEW_PROFILE)
    flat_grout = build_single_panel_geometry(flat).body("grout-thinset")
    records["grout"] = {
        "concave": {"triangles": len(grout.triangles), "sha256": signature(mesh_record(grout)),
                    "mesh_validation": canonicalize(mesh_validation(grout)),
                    "spatial": canonicalize(concave_grout_spatial_validation(production, grout.triangles))},
        "flat": {"triangles": len(flat_grout.triangles), "sha256": signature(mesh_record(flat_grout)),
                 "z": sorted({round(p[2],9) for t in flat_grout.triangles for p in t}),
                 "mesh_validation": canonicalize(mesh_validation(flat_grout))},
    }
    large = resolve_designer_project(DesignerProjectShell.create("square", "l", "point_top"),
                                     PRODUCTION_PROFILE)
    plans = {mode: panelize_model(large, mode=mode) for mode in ("studio", "museum")}
    records["panelization"] = {}
    for mode, plan in plans.items():
        records["panelization"][mode] = {
            "safe": list(plan.safe_envelope_mm),
            "theoretical": [plan.theoretical_rows, plan.theoretical_columns],
            "final": [plan.rows, plan.columns], "count": len(plan.panels),
            "tile_counts": [len(x.tile_ids) for x in plan.panels],
            "seams_sha256": signature(_shared_seams(plan)),
            "plan_sha256": signature(plan_record(plan)),
            "zero_tile_cuts": len(dict(plan.tile_ownership)) == len(plan.model.tiles),
        }
    cells = _marking_cells_at(PanelIdentity("A1", 0, 0), 0.0, 0.0)
    records["backside_id"] = {
        "cell_mm": PANEL_ID_CELL_MM, "deboss_depth_mm": PANEL_ID_DEBOSS_DEPTH_MM,
        "cell_count": len(cells), "glyph_sha256": signature(cells),
        "front_view_panel_order": "excluded-known-bug",
    }
    small_plan = panelize_model(production, mode="studio")
    fabrication = build_panelized_fabrication(small_plan)
    records["stl"] = {}
    for body in fabrication.panels[0].bodies:
        path = write_mesh_stl(body, root / f"{body.body_id}.stl")
        parsed = replace(body, triangles=parse_ascii_stl(path))
        records["stl"][body.material_channel_id] = {
            "name": body.name, "bounds": canonicalize(body.bounds_mm),
            "triangles": len(body.triangles), "mesh_sha256": signature(mesh_record(body)),
            "parsed_sha256": signature(mesh_record(parsed)),
            "bytes_sha256": sha256(path.read_bytes()).hexdigest(),
            "validation": canonicalize(mesh_validation(body)),
            "parsed_validation": canonicalize(mesh_validation(parsed)),
        }
    package = export_panelized_three_mf_package(
        fabrication, root / "three-mf", mode="studio", project_name="Hex Lock",
    )
    three_path = package.three_mf_paths[0]
    manifest = json.loads(package.manifest_path.read_text())
    with ZipFile(three_path) as archive:
        members = archive.namelist()
        model_xml = archive.read("3D/3dmodel.model")
        records["three_mf"] = {
            "members": members, "no_ns0": b"ns0:" not in model_xml,
            "inspected_sha256": signature(inspect_panel_3mf(three_path)),
            "settings_sha256": sha256(archive.read(BAMBU_MODEL_SETTINGS)).hexdigest(),
            "member_hashes": {name: sha256(archive.read(name)).hexdigest() for name in members},
            "package_sha256": sha256(three_path.read_bytes()).hexdigest(),
            "manifest_schema": manifest["schema"], "part_mapping": manifest["part_mapping"],
            "manifest_sha256": signature(manifest),
        }
    snapshot = DesignerExportSnapshot(shell, generated, overrides, "Hex Lock")
    svg = mosaic_svg(snapshot); root_svg = ET.fromstring(svg)
    records["svg"] = {
        "width": root_svg.attrib["width"], "height": root_svg.attrib["height"],
        "viewBox": root_svg.attrib["viewBox"],
        "polygons": len(root_svg.findall(".//{http://www.w3.org/2000/svg}polygon")),
        "group_ids": [x.attrib.get("id") for x in root_svg.findall("{http://www.w3.org/2000/svg}g")],
        "bytes_sha256": sha256(svg.encode()).hexdigest(),
    }
    records["raster"] = {}
    for fmt in ("png", "jpg"):
        result = export_flat_design(snapshot, root / f"lock.{fmt}", fmt)
        image = Image.open(result.path)
        samples = [(0,0), (image.width//2,image.height//2), (image.width-1,image.height-1)]
        records["raster"][fmt] = {"size": list(image.size), "mode": image.mode,
                                         "samples": [list(image.getpixel(x)) for x in samples]}
    records["print_guide"] = {}
    for mode in ("studio", "museum"):
        mode_fabrication = build_panelized_fabrication(panelize_model(production, mode=mode))
        mode_package = export_panelized_three_mf_package(
            mode_fabrication, root / f"guide-{mode}", mode=mode, project_name="Hex Lock",
        )
        mode_manifest = json.loads(mode_package.manifest_path.read_text())
        content = build_print_guide_content(mode_fabrication.plan, mode_manifest)
        semantic = {
            "mode": [content.mode_id, content.mode_name],
            "grid": [content.panel_rows, content.panel_columns],
            "panels": [x.panel_id for x in content.panels],
            "tile": [content.tile_preset_id, content.tile_flat_to_flat_mm, content.tile_orientation],
            "grout": content.grout_gap_mm, "palette": content.palette,
            "parts": content.part_mapping, "instructions": content.mode_instructions,
            "warning": content.core_warning, "placement": mode_manifest["panel_placement"],
        }
        assert BAMBU_CORE_WARNING in content.core_warning
        assert "Archimedean" not in json.dumps(semantic)
        records["print_guide"][mode] = {"semantic": canonicalize(semantic),
                                                 "sha256": signature(semantic)}
    return canonicalize(records)

@pytest.fixture(scope="session")
def hex_regression_records(tmp_path_factory):
    return build_records(tmp_path_factory.mktemp("hex-regression-lock"))

@pytest.fixture(scope="session")
def hex_regression_golden():
    return json.loads(GOLDEN.read_text())

@pytest.mark.parametrize("section", (
    "fixture", "geometry", "clipped", "borders", "artwork",
    "effective_colors", "keyboard", "project_schema_v1",
    "resolved_models", "perimeters", "crown", "grout",
    "panelization", "backside_id", "stl", "three_mf", "svg",
    "raster", "print_guide",
))
def test_frozen_hex_regression_section(
    section, hex_regression_records, hex_regression_golden,
):
    assert hex_regression_records[section] == hex_regression_golden[section]

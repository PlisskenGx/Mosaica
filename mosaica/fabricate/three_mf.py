from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal
import warnings
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .. import __version__
from ..project import MosaicProject
from .mesh import MeshBody, SinglePanelGeometry
from .model import LogicalMaterialChannel, ResolvedFabricationModel
from .panelize import (
    PanelPlan,
    PanelizedFabrication,
    build_panelized_fabrication,
    panelize_model,
)
from .modes import (
    FabricationMode, FabricationModeDefinition, resolve_fabrication_mode,
    resolve_legacy_surface_finish,
)
from .phase2b import PANEL_ID_CELL_MM, PANEL_ID_DEBOSS_DEPTH_MM, PRODUCTION_PROFILE
from .print_guide import GUIDE_FILENAME, generate_print_guide
from .resolve import resolve_mosaic_project


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
THREE_MF_RELATIONSHIP = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
P1S_PANEL_CENTER_MM = (132.0, 110.0)
THREE_MF_SCHEMA = "mosaica-fabricate-3mf"
THREE_MF_SCHEMA_VERSION = 2
SurfaceFinish = Literal["standard", "ironed"]

ET.register_namespace("", CORE_NS)


@dataclass(frozen=True)
class ThreeMFExportPackage:
    output_directory: Path
    manifest_path: Path
    three_mf_paths: tuple[Path, ...]
    geometry_signature: str
    print_guide_path: Path


def _q(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _number(value: float) -> str:
    result = f"{value:.9f}".rstrip("0").rstrip(".")
    return "0" if result in {"", "-0"} else result


def _transform_text(transform: tuple[float, ...]) -> str:
    return " ".join(_number(value) for value in transform)


def panel_plate_transform(
    bounds_mm: tuple[float, float, float, float],
    rotation_degrees: int = 0,
    center_mm: tuple[float, float] = P1S_PANEL_CENTER_MM,
) -> tuple[float, ...]:
    if rotation_degrees not in {0, 90}:
        raise ValueError("Panel print rotation must be 0 or 90 degrees.")
    left, top, right, bottom = bounds_mm
    panel_center_x = (left + right) / 2.0
    panel_center_y = (top + bottom) / 2.0
    if rotation_degrees == 0:
        return (
            1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
            center_mm[0] - panel_center_x,
            center_mm[1] - panel_center_y,
            0.0,
        )
    # 3MF uses a row-vector affine matrix. Rotate 90 degrees about the panel
    # center, then translate that center to the fixed P1S reference.
    return (
        0.0, 1.0, 0.0,
        -1.0, 0.0, 0.0,
        0.0, 0.0, 1.0,
        center_mm[0] + panel_center_y,
        center_mm[1] - panel_center_x,
        0.0,
    )


def apply_transform(
    point: tuple[float, float, float], transform: tuple[float, ...],
) -> tuple[float, float, float]:
    x, y, z = point
    return (
        x * transform[0] + y * transform[3] + z * transform[6] + transform[9],
        x * transform[1] + y * transform[4] + z * transform[7] + transform[10],
        x * transform[2] + y * transform[5] + z * transform[8] + transform[11],
    )


def _channel_order(channel: LogicalMaterialChannel) -> tuple[int, int, str]:
    if channel.channel_id == "base":
        return (0, 0, channel.channel_id)
    if channel.channel_id == "grout-thinset":
        return (1, 0, channel.channel_id)
    if channel.kind == "tile_color":
        return (2, channel.palette_index or 0, channel.channel_id)
    return (3, 0, channel.channel_id)


def _channel_color(channel: LogicalMaterialChannel) -> str:
    color = channel.display_color or ("#808080" if channel.channel_id == "base" else "#FFFFFF")
    color = color.upper()
    return color + "FF" if len(color) == 7 else color


def _body_label(channel: LogicalMaterialChannel) -> str:
    return "Grout-Thinset" if channel.channel_id == "grout-thinset" else channel.name


def _mesh_elements(parent: ET.Element, body: MeshBody) -> tuple[int, int]:
    vertices: list[tuple[float, float, float]] = []
    vertex_indices: dict[tuple[float, float, float], int] = {}
    indexed_triangles = []
    for triangle in body.triangles:
        indices = []
        for point in triangle:
            if point not in vertex_indices:
                vertex_indices[point] = len(vertices)
                vertices.append(point)
            indices.append(vertex_indices[point])
        indexed_triangles.append(tuple(indices))
    mesh = ET.SubElement(parent, _q(CORE_NS, "mesh"))
    vertex_parent = ET.SubElement(mesh, _q(CORE_NS, "vertices"))
    for x, y, z in vertices:
        ET.SubElement(vertex_parent, _q(CORE_NS, "vertex"), {
            "x": _number(x), "y": _number(y), "z": _number(z),
        })
    triangle_parent = ET.SubElement(mesh, _q(CORE_NS, "triangles"))
    for first, second, third in indexed_triangles:
        ET.SubElement(triangle_parent, _q(CORE_NS, "triangle"), {
            "v1": str(first), "v2": str(second), "v3": str(third),
        })
    return len(vertices), len(indexed_triangles)


def _model_xml(
    geometry: SinglePanelGeometry,
    panel: PanelPlan,
) -> tuple[bytes, tuple[float, ...], list[dict[str, object]]]:
    transform = panel_plate_transform(panel.bounds_mm, panel.print_rotation_degrees)
    model = ET.Element(_q(CORE_NS, "model"), {
        "unit": "millimeter", _q("http://www.w3.org/XML/1998/namespace", "lang"): "en-US",
    })
    metadata = {
        "Title": f"Mosaica Panel {panel.panel_id}",
        "Application": f"Mosaica {__version__}",
    }
    for name, value in metadata.items():
        element = ET.SubElement(model, _q(CORE_NS, "metadata"), {"name": name})
        element.text = value
    resources = ET.SubElement(model, _q(CORE_NS, "resources"))
    channels = {
        body.material_channel_id: geometry.model.channel(body.material_channel_id)
        for body in geometry.bodies
    }
    ordered_channels = sorted(channels.values(), key=_channel_order)
    material_index = {channel.channel_id: index for index, channel in enumerate(ordered_channels)}
    materials = ET.SubElement(resources, _q(CORE_NS, "basematerials"), {"id": "1"})
    for channel in ordered_channels:
        ET.SubElement(materials, _q(CORE_NS, "base"), {
            "name": _body_label(channel), "displaycolor": _channel_color(channel),
        })
    records = []
    for object_id, body in enumerate(geometry.bodies, start=2):
        channel = channels[body.material_channel_id]
        object_element = ET.SubElement(resources, _q(CORE_NS, "object"), {
            "id": str(object_id),
            "type": "model",
            "name": f"Panel {panel.panel_id} {_body_label(channel)}",
            "pid": "1",
            "pindex": str(material_index[channel.channel_id]),
        })
        vertex_count, triangle_count = _mesh_elements(object_element, body)
        records.append({
            "object_id": object_id,
            "name": object_element.attrib["name"],
            "channel_id": channel.channel_id,
            "logical_channel_name": _body_label(channel),
            "vertex_count": vertex_count,
            "triangle_count": triangle_count,
            "source_bounds_mm": list(body.bounds_mm),
            "source_geometry_sha256": sha256(
                json.dumps(body.triangles, separators=(",", ":")).encode()
            ).hexdigest(),
        })
    assembly_id = len(geometry.bodies) + 2
    assembly = ET.SubElement(resources, _q(CORE_NS, "object"), {
        "id": str(assembly_id), "type": "model", "name": f"Panel {panel.panel_id}",
    })
    components = ET.SubElement(assembly, _q(CORE_NS, "components"))
    for record in records:
        ET.SubElement(components, _q(CORE_NS, "component"), {
            "objectid": str(record["object_id"]),
        })
    build = ET.SubElement(model, _q(CORE_NS, "build"))
    ET.SubElement(build, _q(CORE_NS, "item"), {
        "objectid": str(assembly_id),
        "transform": _transform_text(transform),
        "printable": "1",
    })
    ET.indent(model, space="  ")
    return ET.tostring(model, encoding="utf-8", xml_declaration=True), transform, records


def _relationships_xml() -> bytes:
    root = ET.Element("Relationships", {"xmlns": REL_NS})
    ET.SubElement(root, "Relationship", {
        "Target": "/3D/3dmodel.model", "Id": "rel0", "Type": THREE_MF_RELATIONSHIP,
    })
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _content_types_xml() -> bytes:
    root = ET.Element("Types", {"xmlns": CONTENT_TYPES_NS})
    ET.SubElement(root, "Default", {
        "Extension": "rels",
        "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
    })
    ET.SubElement(root, "Default", {
        "Extension": "model",
        "ContentType": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    })
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _zip_write(archive: ZipFile, name: str, content: bytes) -> None:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, content)


def write_panel_3mf(
    geometry: SinglePanelGeometry,
    panel: PanelPlan,
    path: str | Path,
    *,
    surface_finish: SurfaceFinish | None = None,
) -> tuple[Path, tuple[float, ...], list[dict[str, object]]]:
    if surface_finish is not None and surface_finish not in {"standard", "ironed"}:
        raise ValueError("Surface finish must be 'standard' or 'ironed'.")
    model_xml, transform, records = _model_xml(geometry, panel)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        _zip_write(archive, "[Content_Types].xml", _content_types_xml())
        _zip_write(archive, "_rels/.rels", _relationships_xml())
        _zip_write(archive, "3D/3dmodel.model", model_xml)
    return path, transform, records


def inspect_panel_3mf(path: str | Path) -> dict[str, object]:
    path = Path(path)
    with ZipFile(path, "r") as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            raise ValueError(f"3MF ZIP integrity failed at {bad_file}.")
        names = tuple(archive.namelist())
        required = {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        if set(names) != required:
            raise ValueError(f"3MF package parts are invalid: {names}.")
        model_xml = archive.read("3D/3dmodel.model")
        ET.fromstring(archive.read("[Content_Types].xml"))
        relationships = ET.fromstring(archive.read("_rels/.rels"))
    relationship = relationships.find(_q(REL_NS, "Relationship"))
    if relationship is None or relationship.attrib.get("Target") != "/3D/3dmodel.model":
        raise ValueError("3MF model relationship is missing.")
    root = ET.fromstring(model_xml)
    metadata = {
        element.attrib["name"]: element.text or ""
        for element in root.findall(_q(CORE_NS, "metadata"))
    }
    resources = root.find(_q(CORE_NS, "resources"))
    build = root.find(_q(CORE_NS, "build"))
    if resources is None or build is None:
        raise ValueError("3MF resources or build section is missing.")
    material_parent = resources.find(_q(CORE_NS, "basematerials"))
    materials = [] if material_parent is None else [
        {"name": item.attrib["name"], "displaycolor": item.attrib["displaycolor"]}
        for item in material_parent.findall(_q(CORE_NS, "base"))
    ]
    meshes, components = {}, {}
    for item in resources.findall(_q(CORE_NS, "object")):
        object_id = int(item.attrib["id"])
        mesh = item.find(_q(CORE_NS, "mesh"))
        component_parent = item.find(_q(CORE_NS, "components"))
        if mesh is not None:
            vertex_parent = mesh.find(_q(CORE_NS, "vertices"))
            triangle_parent = mesh.find(_q(CORE_NS, "triangles"))
            if vertex_parent is None or triangle_parent is None:
                raise ValueError(f"3MF mesh object {object_id} is incomplete.")
            vertices = tuple(
                (float(value.attrib["x"]), float(value.attrib["y"]), float(value.attrib["z"]))
                for value in vertex_parent.findall(_q(CORE_NS, "vertex"))
            )
            triangles = tuple(
                tuple(vertices[int(value.attrib[key])] for key in ("v1", "v2", "v3"))
                for value in triangle_parent.findall(_q(CORE_NS, "triangle"))
            )
            meshes[object_id] = {
                "name": item.attrib.get("name"), "triangles": triangles,
                "material_index": int(item.attrib["pindex"]),
            }
        elif component_parent is not None:
            components[object_id] = tuple(
                int(value.attrib["objectid"])
                for value in component_parent.findall(_q(CORE_NS, "component"))
            )
    build_items = build.findall(_q(CORE_NS, "item"))
    if len(build_items) != 1:
        raise ValueError("Each Phase 3B 3MF must contain exactly one panel build item.")
    transform = tuple(float(value) for value in build_items[0].attrib["transform"].split())
    return {
        "package_parts": names,
        "metadata": metadata,
        "materials": materials,
        "meshes": meshes,
        "components": components,
        "build_object_id": int(build_items[0].attrib["objectid"]),
        "transform": transform,
    }


def validate_panel_3mf(
    path: str | Path,
    geometry: SinglePanelGeometry,
    panel: PanelPlan,
    expected_transform: tuple[float, ...],
) -> dict[str, object]:
    inspected = inspect_panel_3mf(path)
    meshes = inspected["meshes"]
    if len(inspected["transform"]) != len(expected_transform) or any(
        abs(first - second) > 1e-8
        for first, second in zip(inspected["transform"], expected_transform)
    ):
        raise ValueError(f"Panel {panel.panel_id} 3MF placement transform changed.")
    component_ids = inspected["components"].get(inspected["build_object_id"], ())
    if len(component_ids) != len(geometry.bodies):
        raise ValueError(f"Panel {panel.panel_id} 3MF is missing component bodies.")
    for object_id, body in zip(component_ids, geometry.bodies):
        extracted = meshes[object_id]["triangles"]
        if len(extracted) != len(body.triangles):
            raise ValueError(f"Panel {panel.panel_id} {body.name} triangle count changed.")
        for source_triangle, extracted_triangle in zip(body.triangles, extracted):
            for source, result in zip(source_triangle, extracted_triangle):
                if any(abs(first - second) > 1e-8 for first, second in zip(source, result)):
                    raise ValueError(f"Panel {panel.panel_id} {body.name} geometry changed.")
    return {
        "zip_integrity": True,
        "xml_valid": True,
        "body_count": len(component_ids),
        "geometry_round_trip": True,
        "panel_id": panel.panel_id,
    }


def _resolve_export_mode(
    mode: FabricationMode | str | None,
    surface_finish: SurfaceFinish | None,
    *,
    fallback: FabricationMode | None = None,
) -> FabricationModeDefinition:
    selected = resolve_fabrication_mode(fallback if mode is None else mode)
    if surface_finish is None:
        return selected
    legacy = resolve_legacy_surface_finish(surface_finish)
    warnings.warn(
        "--surface-finish is deprecated; use --mode fast or --mode museum.",
        DeprecationWarning,
        stacklevel=3,
    )
    if mode is not None and selected.mode is not legacy.mode:
        raise ValueError("Fabrication mode conflicts with the legacy surface finish.")
    if mode is None and fallback is None:
        return legacy
    if selected.mode is not legacy.mode:
        raise ValueError("Panelization mode conflicts with the legacy surface finish.")
    return selected


def export_panelized_three_mf_package(
    fabrication: PanelizedFabrication,
    output_directory: str | Path,
    *,
    mode: FabricationMode | str | None = None,
    surface_finish: SurfaceFinish | None = None,
    project_name: str | None = None,
) -> ThreeMFExportPackage:
    plan = fabrication.plan
    mode_definition = _resolve_export_mode(
        mode, surface_finish, fallback=plan.fabrication_mode,
    )
    if mode_definition.mode is not plan.fabrication_mode:
        raise ValueError("Fabrication mode must be resolved before panelization.")
    if plan.safe_envelope_mm != mode_definition.safe_envelope_mm:
        raise ValueError("Panel safe envelope does not match the fabrication mode.")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    geometries = {value.panel_id: value for value in fabrication.panels}
    panel_records, paths = [], []
    for panel in plan.panels:
        geometry = geometries[panel.panel_id]
        filename = f"Mosaica_{panel.panel_id}.3mf"
        path, transform, bodies = write_panel_3mf(
            geometry, panel, output / filename,
        )
        validation = validate_panel_3mf(path, geometry, panel, transform)
        paths.append(path)
        panel_records.append({
            "panel_id": panel.panel_id,
            "plate_id": panel.panel_id,
            "filename": filename,
            "logical_grid": {"row": panel.row, "column": panel.column},
            "logical_artwork_bounds_mm": list(panel.bounds_mm),
            "actual_dimensions_mm": {"width": panel.width_mm, "height": panel.height_mm},
            "neighbors": dict(panel.neighbors),
            "print_rotation_degrees": panel.print_rotation_degrees,
            "embedded_build_transform": list(transform),
            "automatic_plate_placement_guaranteed": False,
            "logical_bodies": bodies,
            "logical_channel_count": len(bodies),
            "backside_marking": {
                "content": panel.panel_id,
                "cell_size_mm": PANEL_ID_CELL_MM,
                "deboss_depth_mm": PANEL_ID_DEBOSS_DEPTH_MM,
            },
            "validation": validation,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    signature_payload = [(record["panel_id"], record["sha256"]) for record in panel_records]
    signature = sha256(
        json.dumps(signature_payload, separators=(",", ":")).encode()
    ).hexdigest()
    model = plan.model
    manifest = {
        "schema": {"name": THREE_MF_SCHEMA, "version": THREE_MF_SCHEMA_VERSION},
        "application_version": __version__,
        "fabrication_mode": {
            "id": mode_definition.mode_id,
            "display_name": mode_definition.display_name,
            "quality_tradeoff": mode_definition.quality_tradeoff,
        },
        "project": {
            "name": project_name or "Mosaica Project",
            "finished_dimensions_mm": {
                "width": model.artwork_width_mm,
                "height": model.artwork_height_mm,
            },
            "tile_system": {
                "preset_id": model.tile_preset_id,
                "flat_to_flat_mm": model.tile_flat_to_flat_mm,
                "orientation": model.tile_orientation,
                "grout_gap_mm": model.grout_gap_mm,
            },
            "palette": [
                {
                    "channel_id": channel.channel_id,
                    "name": channel.name,
                    "display_color": channel.display_color or "#808080",
                }
                for channel in model.channels
                if channel.kind == "tile_color"
            ],
        },
        "artifacts": {
            "print_guide": GUIDE_FILENAME,
            "manifest": "manifest.json",
        },
        "architecture": {
            "package_strategy": "one_standard_3mf_per_panel",
            "one_panel_per_plate": True,
            "multi_plate_bambu_project": False,
            "reason": "Bambu multi-plate metadata is not emitted without a documented stable contract.",
            "standard_3mf_core": True,
            "bambu_specific_metadata_emitted": False,
        },
        "safe_panel_envelope_mm": {
            "width": plan.safe_envelope_mm[0], "height": plan.safe_envelope_mm[1],
        },
        "panel_placement": {
            "responsibility": "user_positions_imported_panel_in_bambu_studio",
            "automatic_position_guaranteed": False,
            "proprietary_bambu_placement_metadata_emitted": False,
        },
        "process_intent": mode_definition.process_intent(),
        "print_guide_instructions": [
            {"setting": setting, "tab": tab, "control": control, "action": action}
            for setting, tab, control, action
            in mode_definition.print_guide_instructions
        ],
        "process": {
            "printer": "Bambu Lab P1S",
            "nozzle_mm": 0.4,
            "base_profile": "0.20 mm Standard",
            "wall_loops": 2,
            "intent_embedded_in_3mf": False,
        },
        "logical_channel_order": [
            "Base", "Grout-Thinset", "Tile Color 1", "Tile Color 2",
            "Tile Color 3", "Tile Color 4",
        ],
        "filament_mapping": {
            "responsibility": "user_maps_logical_channels_in_bambu_studio",
            "single_ams_required": False,
            "logical_channels_may_share_physical_filament": True,
        },
        "panelization": {
            "theoretical_rows": plan.theoretical_rows,
            "theoretical_columns": plan.theoretical_columns,
            "final_rows": plan.rows,
            "final_columns": plan.columns,
            "panel_count": len(plan.panels),
            "tile_cuts_created": 0,
            "dedicated_connector_geometry": False,
        },
        "physical_profile": plan.model.profile.__dict__,
        "panels": panel_records,
        "estimates": {"print_time": None, "filament": None, "source": "requires_slicer"},
        "geometry_signature_sha256": signature,
        "bambu_studio_review": [
            "Open one Mosaica_<panel-id>.3mf at a time; each file is one physical plate.",
            "Position each imported panel manually in Bambu Studio.",
            "Map Base, Grout-Thinset, and each used Tile Color to available filaments.",
            *[
                f"{setting}: {action} ({tab} tab, {control})."
                for setting, tab, control, action
                in mode_definition.print_guide_instructions
            ],
            "Confirm the backside is on the plate and the artwork face points upward.",
        ],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print_guide_path = generate_print_guide(
        plan, manifest, output / GUIDE_FILENAME,
    )
    return ThreeMFExportPackage(
        output, manifest_path, tuple(paths), signature, print_guide_path,
    )


def export_three_mf_package(
    model: ResolvedFabricationModel,
    output_directory: str | Path,
    *,
    mode: FabricationMode | str | None = None,
    surface_finish: SurfaceFinish | None = None,
    project_name: str | None = None,
) -> ThreeMFExportPackage:
    mode_definition = _resolve_export_mode(mode, surface_finish)
    plan = panelize_model(model, mode=mode_definition.mode)
    return export_panelized_three_mf_package(
        build_panelized_fabrication(plan), output_directory,
        mode=mode_definition.mode,
        project_name=project_name,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export one production-ready multipart 3MF per Mosaica panel.",
    )
    parser.add_argument("--project", required=True, help="saved MosaicProject JSON")
    parser.add_argument("--out", default="fabricate_3mf_export")
    parser.add_argument(
        "--mode", choices=tuple(value.value for value in FabricationMode), default=None,
        help="fabrication strategy (default: fast)",
    )
    parser.add_argument(
        "--surface-finish", choices=("standard", "ironed"), default=None,
        help="deprecated compatibility option: standard=fast, ironed=museum",
    )
    arguments = parser.parse_args(argv)
    project = MosaicProject.load(arguments.project)
    model = resolve_mosaic_project(project, PRODUCTION_PROFILE)
    package = export_three_mf_package(
        model, arguments.out, mode=arguments.mode,
        surface_finish=arguments.surface_finish,
        project_name=Path(arguments.project).stem,
    )
    print(f"Fabricate 3MF export: {package.output_directory}")
    print(f"Panels: {len(package.three_mf_paths)}")
    print(f"Manifest: {package.manifest_path}")
    print(f"Print guide: {package.print_guide_path}")
    print(f"Geometry signature: {package.geometry_signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

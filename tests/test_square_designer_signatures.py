"""T6 deterministic locks for the Square 2D Designer family."""

from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mosaica.artwork import create_artwork, update_artwork_transform
from mosaica.border import build_border_layer
from mosaica.designer import DesignerProjectShell
from mosaica.designer_export import DesignerExportSnapshot
from mosaica.designer_flat_export import mosaic_svg
from mosaica.designer_generation import generate_designer_artwork
from mosaica.project_file import DesignerProjectFileState, load_project_file, save_project_file


GOLDEN = Path(__file__).parent / "fixtures" / "square_designer_v1.json"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="M0 0h100v100H0z" fill="#000000"/></svg>'


def _signature(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _geometry(shell):
    value = shell.to_dict()["geometry"]
    return {
        "shape": value["shape"], "orientation": value["orientation"],
        "width_in": value["width_in"], "height_in": value["height_in"],
        "columns": value["columns"], "rows": value["rows"],
        "visible": value["visible_piece_count"],
        "full": value["full_tile_count"], "clipped": value["clipped_piece_count"],
        "tiles_sha256": _signature([
            {
                "id": tile["id"], "row": tile["row"], "column": tile["column"],
                "center": tile["center_in"], "full": tile["full_vertices_in"],
                "visible": tile["vertices_in"], "type": tile["piece_type"],
                "fraction": tile["piece_fraction"],
            }
            for tile in value["tiles"]
        ]),
        "keyboard_sha256": _signature(value["keyboard_navigation"]),
    }


def build_records(tmp_path):
    shells = {
        "preset_s_square": DesignerProjectShell.create("square", "s", "straight", family_id="square"),
        "preset_m_square": DesignerProjectShell.create("square", "m", "straight", family_id="square"),
        "preset_l_landscape": DesignerProjectShell.create("landscape", "l", "straight", family_id="square"),
        "physical_24x18": DesignerProjectShell.create_physical("m", "straight", 24, 18, family_id="square"),
        "counted_7x5": DesignerProjectShell.create_custom("m", "straight", 7, 5, family_id="square"),
    }
    records = {"geometry": {name: _geometry(shell) for name, shell in shells.items()}}
    shell = shells["physical_24x18"].with_border("solid")
    border = build_border_layer(shell.geometry, "solid")
    records["border"] = {
        "owned": len(border.border_owned_placement_ids),
        "protected": len(border.protected_placement_ids),
        "sha256": _signature(border.to_dict()),
    }
    artwork_shell = DesignerProjectShell.create_physical(
        "l", "straight", 3, 2, family_id="square",
    )
    no_border = build_border_layer(artwork_shell.geometry, "none")
    artwork = create_artwork("square-lock.svg", SVG, artwork_shell.geometry, no_border)
    artwork = update_artwork_transform(
        artwork, x_in=0, y_in=0,
        width_in=artwork_shell.geometry.width_in,
        height_in=artwork_shell.geometry.height_in,
    )
    with patch(
        "mosaica.designer_generation._rasterize_svg",
        return_value=Image.new("RGBA", (64, 64), (0, 0, 0, 255)),
    ):
        generated = generate_designer_artwork(
            artwork, artwork_shell.geometry, no_border,
            artwork_shell.color_system, 1,
        )
    override = generated.assignments[0].tile_id
    overrides = {override: "project-color-2"}
    effective = artwork_shell.to_dict(generated, overrides)
    records["artwork_paint"] = {
        "assignments": len(generated.assignments),
        "clipped_assignments": sum(
            effective_tile["piece_type"] != "full"
            for effective_tile in effective["geometry"]["tiles"]
            if effective_tile["generated_artwork"]
        ),
        "sha256": _signature({
            "generated": generated.to_dict(),
            "effective": [
                (tile["id"], tile["color_id"], tile["manual_override"])
                for tile in effective["geometry"]["tiles"]
            ],
        }),
    }
    state = DesignerProjectFileState(
        artwork_shell, artwork, generated, overrides, True, "Square Lock",
    )
    path = save_project_file(tmp_path / "square-lock.mosaica", state)
    loaded = load_project_file(path)
    records["persistence"] = {
        "family": loaded.project.tile_family,
        "preset": loaded.project.tile_preset_id,
        "orientation": loaded.project.tile_orientation,
        "geometry_sha256": _signature(_geometry(loaded.project)),
        "overrides": loaded.paint_overrides,
    }
    snapshot = DesignerExportSnapshot(
        loaded.project, loaded.generated_artwork, loaded.paint_overrides,
        "Square Lock",
    )
    svg = mosaic_svg(snapshot)
    records["svg"] = {
        "bytes_sha256": sha256(svg.encode()).hexdigest(),
        "polygons": svg.count("<polygon"),
        "family_metadata": 'data-tile-family="square"' in svg,
    }
    return records


def test_frozen_square_designer_v1(tmp_path):
    assert build_records(tmp_path) == json.loads(GOLDEN.read_text())

"""Canonical test-only representations for frozen Hex output.

Nine decimals retain sub-nanometre precision for inch coordinates and
nanometre precision for millimetre geometry.  This is substantially tighter
than manufacturing relevance while removing binary-float representation noise.
"""
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json

FLOAT_DIGITS = 9

def canonicalize(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, float):
        result = round(value, FLOAT_DIGITS)
        return 0.0 if result == 0 else result
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [canonicalize(item) for item in value]
    return value

def canonical_json(value):
    return json.dumps(canonicalize(value), sort_keys=True, separators=(",", ":"))

def signature(value):
    return sha256(canonical_json(value).encode()).hexdigest()

def geometry_record(shell):
    geometry = shell.geometry
    return {
        "canvas": shell.canvas.to_dict(), "canvas_mode": shell.canvas_mode,
        "custom_counts": [shell.tiles_across, shell.tiles_down],
        "tile": shell.tile.to_dict(), "grout_mm": shell.grout_mm,
        "shape": geometry.shape, "orientation": geometry.orientation,
        "bounds_in": [geometry.width_in, geometry.height_in],
        "grid": [geometry.columns, geometry.rows],
        # Order is semantic: IDs are derived from this enumeration.
        "placements": [{
            "id": f"placement-{index:06d}", "row": tile.row,
            "column": tile.column,
            "center_in": [tile.center_x_in, tile.center_y_in],
            "full_vertices_in": tile.full_vertices_in,
            "vertices_in": tile.vertices_in,
            "piece_type": tile.piece_type,
            "piece_fraction": tile.piece_fraction,
            "principal": tile.principal_grid,
            "principal_address": [tile.principal_row, tile.principal_column],
        } for index, tile in enumerate(geometry.placements)],
    }

def mesh_record(body):
    return {
        "id": body.body_id, "name": body.name,
        "channel": body.material_channel_id, "bounds": body.bounds_mm,
        "tile_ids": body.tile_ids,
        "solid_triangle_counts": body.solid_triangle_counts,
        "triangles": body.triangles,
    }

def plan_record(plan):
    return {
        "mode": plan.fabrication_mode.value,
        "safe_envelope": plan.safe_envelope_mm,
        "theoretical": [plan.theoretical_rows, plan.theoretical_columns],
        "final": [plan.rows, plan.columns],
        "cuts": [plan.x_cuts_mm, plan.y_cuts_mm], "score": plan.score,
        "panels": [{
            "id": panel.panel_id, "row": panel.row, "column": panel.column,
            "tile_ids": panel.tile_ids, "bounds": panel.bounds_mm,
            "area": panel.area_mm2, "neighbors": panel.neighbors,
            "rotation": panel.print_rotation_degrees,
        } for panel in plan.panels],
        "ownership": plan.tile_ownership,
    }

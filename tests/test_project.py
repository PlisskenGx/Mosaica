from dataclasses import replace
import json
from pathlib import Path

import pytest
from PIL import Image

from mosaica.export import (
    export_counts_csv,
    export_grid_csv,
    export_placements_csv,
    export_preview_png,
)
from mosaica.geometry import (
    build_geometry,
    build_panel_geometry,
)
from mosaica.model import (
    MosaicConfig,
    MosaicResult,
    PaletteColor,
)
from mosaica.project import (
    MosaicProject,
    PROJECT_SCHEMA_NAME,
    PROJECT_SCHEMA_VERSION,
)


PALETTE = (
    PaletteColor("Black", (0, 0, 0), "B"),
    PaletteColor("White", (255, 255, 255), "W"),
)


def _result(geometry=None, source_path=Path("artwork.png")):
    config = MosaicConfig(columns=2, rows=1)
    geometry = geometry or build_geometry(config, 2, 1)
    return MosaicResult(
        columns=geometry.columns,
        rows=geometry.rows,
        grid=[
            [0 for _ in range(geometry.columns)]
            for _ in range(geometry.rows)
        ],
        palette=PALETTE,
        source_path=source_path,
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    )


def test_override_preserves_generated_and_changes_effective_value():
    project = MosaicProject.from_result(_result())

    project.set_override(0, 0, 1)

    assert project.generated_value(0, 0) == 0
    assert project.generated_grid[0][0] == 0
    assert project.override_value(0, 0) == 1
    assert project.effective_value(0, 0) == 1


def test_clearing_override_restores_generated_value():
    project = MosaicProject.from_result(_result())
    project.set_override(0, 0, 1)

    project.clear_override(0, 0)

    assert project.override_value(0, 0) is None
    assert project.effective_value(0, 0) == 0


def test_clear_all_overrides():
    project = MosaicProject.from_result(_result())
    project.set_override(0, 0, 1)
    project.set_override(0, 1, 1)

    project.clear_all_overrides()

    assert project.overrides == {}
    assert project.effective_grid == [[0, 0]]


def test_outside_placement_cannot_be_edited():
    geometry = build_geometry(
        MosaicConfig(columns=2, rows=1),
        2,
        1,
    )
    placements = list(geometry.placements)
    placements[0] = replace(
        placements[0],
        piece_type="outside",
        piece_fraction=0.0,
        vertices_in=(),
    )
    geometry = replace(
        geometry,
        placements=tuple(placements),
    )
    project = MosaicProject.from_result(_result(geometry))

    with pytest.raises(ValueError, match="Outside"):
        project.set_override(0, 0, 1)


def test_clipped_perimeter_is_protected():
    config = MosaicConfig(
        tile_shape="hex",
        target_width_in=3,
        target_height_in=2,
    )
    geometry = build_panel_geometry(config, 3, 2)
    result = _result(geometry)
    result.config = config
    project = MosaicProject.from_result(result)
    clipped = next(
        placement
        for placement in geometry.placements
        if placement.piece_type not in {"full", "outside"}
    )

    with pytest.raises(ValueError, match="perimeter"):
        project.set_override(clipped.row, clipped.column, 1)


def test_counts_use_effective_assignments(tmp_path):
    project = MosaicProject.from_result(_result())
    project.set_override(0, 0, 1)

    assert project.counts() == {"Black": 1, "White": 1}

    path = export_counts_csv(project, tmp_path / "counts.csv")
    assert "White,W,#FFFFFF,1" in path.read_text()


def test_preview_and_csv_exports_use_effective_assignment(tmp_path):
    project = MosaicProject.from_result(_result())
    project.set_override(0, 0, 1)

    preview = export_preview_png(
        project,
        tmp_path / "preview.png",
        pixels_per_inch=20,
        draw_grid=False,
    )
    grid = export_grid_csv(project, tmp_path / "grid.csv")
    placements = export_placements_csv(
        project,
        tmp_path / "placements.csv",
    )

    with Image.open(preview) as image:
        assert image.getpixel((14, 14)) == (255, 255, 255)

    assert "1,White,Black" in grid.read_text()
    assert ",White,W" in placements.read_text()


def test_json_round_trip_preserves_generated_state_and_overrides(tmp_path):
    project = MosaicProject.from_result(_result())
    project.set_override(0, 1, 1)
    path = project.save(tmp_path / "project.json")

    loaded = MosaicProject.load(path)

    assert loaded.generated_grid == project.generated_grid
    assert loaded.overrides == project.overrides
    assert loaded.effective_grid == project.effective_grid
    assert loaded.config == project.config
    assert loaded.geometry == project.geometry
    assert loaded.source_path == Path("artwork.png").resolve()
    assert loaded.bw_evidence_cache is None


def test_project_json_contains_schema_and_no_source_binary(tmp_path):
    path = MosaicProject.from_result(_result()).save(
        tmp_path / "project.json"
    )
    data = json.loads(path.read_text())

    assert data["schema"] == {
        "name": PROJECT_SCHEMA_NAME,
        "version": PROJECT_SCHEMA_VERSION,
    }
    assert data["engine_version"] == "0.6.0"
    assert data["source"]["path"] == "artwork.png"
    assert data["source"]["filename"] == "artwork.png"
    assert data["source"]["relative_path"]


def test_project_source_metadata_is_portable_and_not_required(
    tmp_path,
):
    source = tmp_path / "source.png"
    source.write_bytes(b"source metadata only")
    project_path = tmp_path / "project.json"
    MosaicProject.from_result(
        _result(source_path=source)
    ).save(project_path)
    data = json.loads(project_path.read_text())

    assert data["source"] == {
        "path": str(source),
        "filename": "source.png",
        "relative_path": "source.png",
    }

    source.unlink()
    loaded = MosaicProject.load(project_path)

    assert loaded.source_path == source
    assert not loaded.source_path.exists()
    assert export_preview_png(
        loaded,
        tmp_path / "restored-preview.png",
    ).exists()


def test_exports_use_authoritative_effective_index(tmp_path):
    project = MosaicProject.from_result(_result())
    calls = []
    original = project.effective_index

    def tracked(row, column):
        calls.append((row, column))
        return original(row, column)

    project.effective_index = tracked

    project.counts()
    export_grid_csv(project, tmp_path / "grid.csv")
    export_placements_csv(project, tmp_path / "placements.csv")
    export_preview_png(project, tmp_path / "preview.png")

    assert calls

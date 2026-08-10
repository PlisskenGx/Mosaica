import json
import sys

from PIL import Image

from mosaic_engine.cli import main
from mosaic_engine import editor
from mosaic_engine.geometry import build_geometry
from mosaic_engine.model import MosaicConfig, MosaicResult, PaletteColor
from mosaic_engine.project import MosaicProject


def test_cli_can_save_load_and_edit_project(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 1), "black").save(source)
    first_project = tmp_path / "first.json"
    second_project = tmp_path / "second.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mosaic-engine",
            str(source),
            "--color", "Black:#000000",
            "--color", "White:#FFFFFF",
            "--cols", "2",
            "--rows", "1",
            "--set-override", "1,1:2",
            "--save-project", str(first_project),
            "--out", str(tmp_path / "first-output"),
        ],
    )
    main()

    assert MosaicProject.load(first_project).effective_value(0, 0) == 1

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mosaic-engine",
            "--load-project", str(first_project),
            "--clear-override", "1,1",
            "--save-project", str(second_project),
            "--out", str(tmp_path / "second-output"),
        ],
    )
    main()

    loaded = MosaicProject.load(second_project)
    assert loaded.override_value(0, 0) is None
    assert loaded.effective_value(0, 0) == 0
    assert json.loads(second_project.read_text())["schema"]["version"] == 1


def test_cli_launches_local_editor(monkeypatch):
    calls = []

    def fake_run_editor(project_path, **options):
        calls.append((project_path, options))

    monkeypatch.setattr(editor, "run_editor", fake_run_editor)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mosaic-engine",
            "--edit-project", "saved-project.json",
            "--editor-port", "9123",
            "--no-browser",
        ],
    )

    main()

    assert calls == [
        (
            "saved-project.json",
            {
                "port": 9123,
                "open_browser": False,
            },
        )
    ]


def test_cli_emits_machine_readable_benchmark_json(
    tmp_path, monkeypatch, capsys
):
    config = MosaicConfig(columns=1, rows=1, quantization_mode="bw")
    geometry = build_geometry(config, 1, 1)
    project = MosaicProject.from_result(MosaicResult(
        columns=1,
        rows=1,
        grid=[[0]],
        palette=(
            PaletteColor("Black", (0, 0, 0)),
            PaletteColor("White", (255, 255, 255)),
        ),
        source_path=tmp_path / "missing.png",
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    path = project.save(tmp_path / "benchmark.json")
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine",
        "--benchmark", str(path),
        "--benchmark-json",
    ])

    main()

    output = json.loads(capsys.readouterr().out)
    assert output[0]["project_path"] == str(path)
    assert output[0]["real_changed_overrides"] == 0


def test_cli_caches_evidence_without_changing_tiles_and_refines_offline(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "black").save(source)
    config = MosaicConfig(
        tile_shape="hex",
        columns=3,
        rows=3,
        quantization_mode="bw",
    )
    geometry = build_geometry(config, 3, 3)
    project = MosaicProject.from_result(MosaicResult(
        columns=3,
        rows=3,
        grid=[[0, 0, 0], [0, 1, 0], [0, 0, 0]],
        palette=(
            PaletteColor("Black", (0, 0, 0)),
            PaletteColor("White", (255, 255, 255)),
        ),
        source_path=source,
        physical_width_in=geometry.width_in,
        physical_height_in=geometry.height_in,
        config=config,
        geometry=geometry,
    ))
    project.set_override(1, 1, 0)
    path = project.save(tmp_path / "project.json")
    generated = project.generated_grid
    effective = project.effective_grid
    overrides = project.overrides
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine", "--cache-evidence", str(path),
    ])

    main()

    cached = MosaicProject.load(path)
    assert cached.bw_evidence_cache is not None
    assert cached.generated_grid == generated
    assert cached.effective_grid == effective
    assert cached.overrides == overrides
    source.unlink()
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", [
        "mosaic-engine",
        "--refine-proposals", str(path),
        "--refine-json",
    ])

    main()

    output = json.loads(capsys.readouterr().out)
    assert "refinement" in output

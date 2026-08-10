import json
import sys

from PIL import Image

from mosaic_engine.cli import main
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

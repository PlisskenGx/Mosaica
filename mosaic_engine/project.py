from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Iterable

from .boundary import Rect
from .geometry import GridGeometry, TilePlacement
from .model import MosaicConfig, MosaicResult, PaletteColor


PROJECT_SCHEMA_NAME = "mosaic-engine-project"
PROJECT_SCHEMA_VERSION = 1


class MosaicProject:
    """Editable state layered over an engine-generated mosaic."""

    def __init__(
        self,
        generated_result: MosaicResult,
        overrides: dict[tuple[int, int], int] | None = None,
        protect_perimeter: bool = True,
    ) -> None:
        self._generated_result = generated_result
        self._generated_grid = tuple(
            tuple(row)
            for row in generated_result.grid
        )
        self._overrides: dict[tuple[int, int], int] = {}
        self.protect_perimeter = protect_perimeter

        for (row, column), palette_index in (
            overrides or {}
        ).items():
            self.set_override(row, column, palette_index)

    @classmethod
    def from_result(
        cls,
        result: MosaicResult,
    ) -> MosaicProject:
        return cls(result)

    @property
    def generated_grid(self) -> tuple[tuple[int, ...], ...]:
        return self._generated_grid

    @property
    def overrides(self) -> dict[tuple[int, int], int]:
        return dict(self._overrides)

    @property
    def effective_grid(self) -> list[list[int]]:
        return [
            [
                self.effective_index(row, column)
                for column in range(self.columns)
            ]
            for row in range(self.rows)
        ]

    @property
    def grid(self) -> list[list[int]]:
        """Compatibility view used by existing exporters."""

        return self.effective_grid

    @property
    def columns(self) -> int:
        return self._generated_result.columns

    @property
    def rows(self) -> int:
        return self._generated_result.rows

    @property
    def palette(self):
        return self._generated_result.palette

    @property
    def source_path(self) -> Path:
        return self._generated_result.source_path

    @property
    def physical_width_in(self) -> float:
        return self._generated_result.physical_width_in

    @property
    def physical_height_in(self) -> float:
        return self._generated_result.physical_height_in

    @property
    def config(self) -> MosaicConfig:
        return self._generated_result.config

    @property
    def geometry(self) -> GridGeometry:
        return self._generated_result.geometry

    def generated_value(self, row: int, column: int) -> int:
        self._validate_coordinates(row, column)
        return self._generated_grid[row][column]

    def override_value(
        self,
        row: int,
        column: int,
    ) -> int | None:
        self._validate_coordinates(row, column)
        return self._overrides.get((row, column))

    def effective_index(self, row: int, column: int) -> int:
        """Return the authoritative final palette index for a tile."""

        self._validate_coordinates(row, column)
        return self._overrides.get(
            (row, column),
            self._generated_grid[row][column],
        )

    def effective_value(self, row: int, column: int) -> int:
        """Backward-compatible alias for effective_index()."""

        return self.effective_index(row, column)

    def set_override(
        self,
        row: int,
        column: int,
        palette_index: int,
    ) -> None:
        placement = self._editable_placement(row, column)

        if not 0 <= palette_index < len(self.palette):
            raise ValueError(
                "Palette index is outside the project palette."
            )

        if placement.piece_type == "outside":
            raise ValueError("Outside placements cannot be edited.")

        if (
            self.protect_perimeter
            and placement.piece_type != "full"
        ):
            raise ValueError(
                "Clipped perimeter pieces are protected from editing."
            )

        self._overrides[(row, column)] = palette_index

    def clear_override(self, row: int, column: int) -> None:
        self._validate_coordinates(row, column)
        self._overrides.pop((row, column), None)

    def clear_all_overrides(self) -> None:
        self._overrides.clear()

    def counts(self) -> dict[str, int]:
        counts = {
            color.name: 0
            for color in self.palette
        }

        for placement in self.geometry.placements:
            if placement.piece_type == "outside":
                continue

            palette_index = self.effective_index(
                placement.row,
                placement.column,
            )
            counts[self.palette[palette_index].name] += 1

        return counts

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(
                self.to_dict(project_path=path),
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        return path

    def to_dict(
        self,
        project_path: str | Path | None = None,
    ) -> dict:
        return {
            "schema": {
                "name": PROJECT_SCHEMA_NAME,
                "version": PROJECT_SCHEMA_VERSION,
            },
            "engine_version": "0.5.0",
            "source": self._source_metadata(project_path),
            "palette": [
                {
                    "name": color.name,
                    "rgb": list(color.rgb),
                    "sku": color.sku,
                }
                for color in self.palette
            ],
            "config": asdict(self.config),
            "generated_grid": [
                list(row)
                for row in self._generated_grid
            ],
            "overrides": [
                {
                    "row": row,
                    "column": column,
                    "palette_index": palette_index,
                }
                for (row, column), palette_index
                in sorted(self._overrides.items())
            ],
            "edit_policy": {
                "protect_perimeter": self.protect_perimeter,
            },
            "geometry": _geometry_to_dict(self.geometry),
        }

    @classmethod
    def load(cls, path: str | Path) -> MosaicProject:
        path = Path(path)
        return cls.from_dict(
            json.loads(path.read_text(encoding="utf-8")),
            project_path=path,
        )

    @classmethod
    def from_dict(
        cls,
        data: dict,
        project_path: str | Path | None = None,
    ) -> MosaicProject:
        schema = data.get("schema", {})

        if schema.get("name") != PROJECT_SCHEMA_NAME:
            raise ValueError("Not a Mosaic Engine project file.")

        if schema.get("version") != PROJECT_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported Mosaic Engine project schema version: "
                f"{schema.get('version')}"
            )

        config_values = dict(data["config"])
        config_values["background_rgb"] = tuple(
            config_values["background_rgb"]
        )
        config = MosaicConfig(**config_values)
        palette = tuple(
            PaletteColor(
                name=color["name"],
                rgb=tuple(color["rgb"]),
                sku=color.get("sku"),
            )
            for color in data["palette"]
        )
        generated_grid = [
            list(row)
            for row in data["generated_grid"]
        ]
        geometry = _geometry_from_dict(data["geometry"])
        result = MosaicResult(
            columns=geometry.columns,
            rows=geometry.rows,
            grid=generated_grid,
            palette=palette,
            source_path=_source_path_from_metadata(
                data["source"],
                project_path,
            ),
            physical_width_in=geometry.width_in,
            physical_height_in=geometry.height_in,
            config=config,
            geometry=geometry,
        )
        overrides = {
            (item["row"], item["column"]): item["palette_index"]
            for item in data.get("overrides", [])
        }

        return cls(
            result,
            overrides=overrides,
            protect_perimeter=data.get(
                "edit_policy",
                {},
            ).get("protect_perimeter", True),
        )

    def _source_metadata(
        self,
        project_path: str | Path | None,
    ) -> dict:
        source = self.source_path
        metadata = {
            "path": str(source),
            "filename": source.name,
            "relative_path": None,
        }

        if project_path is None:
            if not source.is_absolute():
                metadata["relative_path"] = source.as_posix()

            return metadata

        source_absolute = (
            source
            if source.is_absolute()
            else Path.cwd() / source
        ).resolve(strict=False)
        project_parent = Path(project_path).resolve(
            strict=False
        ).parent

        try:
            metadata["relative_path"] = Path(
                os.path.relpath(
                    source_absolute,
                    project_parent,
                )
            ).as_posix()
        except ValueError:
            pass

        return metadata

    def _validate_coordinates(self, row: int, column: int) -> None:
        if not (
            0 <= row < self.rows
            and 0 <= column < self.columns
        ):
            raise IndexError("Tile coordinates are outside the grid.")

    def _editable_placement(
        self,
        row: int,
        column: int,
    ) -> TilePlacement:
        self._validate_coordinates(row, column)
        return self.geometry.placement(row, column)


def _rect_to_dict(rect: Rect) -> dict:
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
    }


def _source_path_from_metadata(
    metadata: dict,
    project_path: str | Path | None,
) -> Path:
    relative_path = metadata.get("relative_path")

    if relative_path and project_path is not None:
        return (
            Path(project_path).resolve(strict=False).parent
            / relative_path
        ).resolve(strict=False)

    return Path(metadata["path"])


def _geometry_to_dict(geometry: GridGeometry) -> dict:
    return {
        "shape": geometry.shape,
        "columns": geometry.columns,
        "rows": geometry.rows,
        "width_in": geometry.width_in,
        "height_in": geometry.height_in,
        "panel_bounds": _rect_to_dict(geometry.panel_bounds),
        "artwork_bounds": _rect_to_dict(geometry.artwork_bounds),
        "placements": [
            {
                "row": placement.row,
                "column": placement.column,
                "center_x_in": placement.center_x_in,
                "center_y_in": placement.center_y_in,
                "full_vertices_in": [
                    list(point)
                    for point in placement.full_vertices_in
                ],
                "vertices_in": [
                    list(point)
                    for point in placement.vertices_in
                ],
                "piece_type": placement.piece_type,
                "piece_fraction": placement.piece_fraction,
            }
            for placement in geometry.placements
        ],
    }


def _rect_from_dict(data: dict) -> Rect:
    return Rect(
        left=data["left"],
        top=data["top"],
        right=data["right"],
        bottom=data["bottom"],
    )


def _points(values: Iterable[Iterable[float]]):
    return tuple(
        tuple(point)
        for point in values
    )


def _geometry_from_dict(data: dict) -> GridGeometry:
    return GridGeometry(
        shape=data["shape"],
        columns=data["columns"],
        rows=data["rows"],
        width_in=data["width_in"],
        height_in=data["height_in"],
        placements=tuple(
            TilePlacement(
                row=placement["row"],
                column=placement["column"],
                center_x_in=placement["center_x_in"],
                center_y_in=placement["center_y_in"],
                full_vertices_in=_points(
                    placement["full_vertices_in"]
                ),
                vertices_in=_points(placement["vertices_in"]),
                piece_type=placement["piece_type"],
                piece_fraction=placement["piece_fraction"],
            )
            for placement in data["placements"]
        ),
        panel_bounds=_rect_from_dict(data["panel_bounds"]),
        artwork_bounds=_rect_from_dict(data["artwork_bounds"]),
    )

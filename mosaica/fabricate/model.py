from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


Point2MM = tuple[float, float]


@dataclass(frozen=True)
class FabricationProfile:
    """Versioned physical inputs for deterministic panel generation.

    Base and grout thicknesses are deliberately required: neither value is
    recorded by the validated 2D project or historical repository code.
    """

    profile_id: str
    version: int
    base_thickness_mm: float
    grout_thickness_mm: float
    straight_tile_relief_mm: float = 1.6
    rounded_crown_mm: float = 0.8
    crown_segments: int = 6
    grout_surface: str = "flat"
    grout_depression_mm: float = 0.0
    grout_mesh_step_mm: float = 0.9
    frame_land_mm: float = 9.525

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("Fabrication profile ID is required.")
        if self.version <= 0:
            raise ValueError("Fabrication profile version must be positive.")
        for name, value in (
            ("Base thickness", self.base_thickness_mm),
            ("Grout/Thinset thickness", self.grout_thickness_mm),
            ("Straight tile relief", self.straight_tile_relief_mm),
            ("Rounded crown", self.rounded_crown_mm),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive.")
        if self.crown_segments < 2:
            raise ValueError("Rounded crown requires at least two segments.")
        if self.grout_surface not in {"flat", "concave"}:
            raise ValueError("Grout surface must be 'flat' or 'concave'.")
        if self.grout_surface == "flat" and self.grout_depression_mm != 0.0:
            raise ValueError("Flat grout cannot have a surface depression.")
        if self.grout_surface == "concave" and not (
            0.0 < self.grout_depression_mm < self.grout_thickness_mm
        ):
            raise ValueError(
                "Concave grout depression must be positive and less than the "
                "structural Grout/Thinset thickness."
            )
        if self.grout_mesh_step_mm <= 0:
            raise ValueError("Grout surface mesh step must be positive.")
        if self.frame_land_mm < 0:
            raise ValueError("Frame land cannot be negative.")

    @property
    def total_tile_relief_mm(self) -> float:
        return round(self.straight_tile_relief_mm + self.rounded_crown_mm, 9)


@dataclass(frozen=True)
class LogicalMaterialChannel:
    channel_id: str
    name: str
    kind: str
    display_color: str | None = None
    source_color_id: str | None = None
    palette_index: int | None = None
    project_color_name: str | None = None


@dataclass(frozen=True)
class ResolvedTile:
    tile_id: str
    row: int
    column: int
    center_mm: Point2MM
    polygon_mm: tuple[Point2MM, ...]
    full_polygon_mm: tuple[Point2MM, ...]
    piece_type: str
    piece_fraction: float
    material_channel_id: str
    source_color_id: str
    border_owned: bool = False
    border_role: str | None = None


@dataclass(frozen=True)
class ResolvedFabricationModel:
    schema_name: str
    schema_version: int
    profile: FabricationProfile
    artwork_width_mm: float
    artwork_height_mm: float
    tile_preset_id: str | None
    tile_flat_to_flat_mm: float
    tile_orientation: str
    grout_gap_mm: float
    tile_profile: str
    tiles: tuple[ResolvedTile, ...]
    channels: tuple[LogicalMaterialChannel, ...]
    border_preset_id: str
    origin: str = "artwork-top-left"
    axes: tuple[str, str, str] = (
        "X right across artwork",
        "Y down artwork",
        "Z up from build plate",
    )
    units: str = "mm"
    print_orientation: str = "backside-on-build-plate; artwork-face-up"

    @property
    def artwork_bounds_mm(self) -> tuple[float, float, float, float]:
        return (0.0, 0.0, self.artwork_width_mm, self.artwork_height_mm)

    @property
    def physical_bounds_mm(self) -> tuple[float, float, float, float, float, float]:
        return (
            0.0, 0.0, 0.0,
            self.artwork_width_mm,
            self.artwork_height_mm,
            self.profile.base_thickness_mm
            + self.profile.grout_thickness_mm
            + self.profile.total_tile_relief_mm,
        )

    def channel(self, channel_id: str) -> LogicalMaterialChannel:
        try:
            return next(value for value in self.channels if value.channel_id == channel_id)
        except StopIteration as exc:
            raise ValueError(f"Unknown fabrication channel: {channel_id}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": {"name": self.schema_name, "version": self.schema_version},
            "units": self.units,
            "origin": self.origin,
            "axes": list(self.axes),
            "print_orientation": self.print_orientation,
            "profile": asdict(self.profile),
            "artwork": {
                "width_mm": self.artwork_width_mm,
                "height_mm": self.artwork_height_mm,
                "bounds_mm": list(self.artwork_bounds_mm),
            },
            "tile_system": {
                "preset_id": self.tile_preset_id,
                "flat_to_flat_mm": self.tile_flat_to_flat_mm,
                "orientation": self.tile_orientation,
                "grout_gap_mm": self.grout_gap_mm,
                "profile": self.tile_profile,
            },
            "border_preset_id": self.border_preset_id,
            "physical_bounds_mm": list(self.physical_bounds_mm),
            "channels": [asdict(value) for value in self.channels],
            "tiles": [
                {
                    **asdict(value),
                    "center_mm": list(value.center_mm),
                    "polygon_mm": [list(point) for point in value.polygon_mm],
                    "full_polygon_mm": [list(point) for point in value.full_polygon_mm],
                }
                for value in self.tiles
            ],
        }

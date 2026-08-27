"""Family-aware fabrication strategy resolution.

The Hex adapter owns validated physical semantics while delegating all numeric
geometry to the existing Fabricate implementation.
"""

from dataclasses import dataclass
from types import MappingProxyType

from ..tiles import TileSystemSelection, get_tile_family
from .model import PhysicalTileDimension, ResolvedTileSystem


HEXAGON_TILE_PROFILE_ID = "V4 Rounded"


@dataclass(frozen=True)
class HexagonFabricationStrategy:
    strategy_id: str = "hexagon-v4-rounded"
    family_id: str = "hexagon"
    profile_id: str = HEXAGON_TILE_PROFILE_ID

    def resolve_tile_system(self, selection: TileSystemSelection) -> ResolvedTileSystem:
        if selection.family_id != self.family_id:
            raise ValueError(
                f"Hexagon fabrication cannot resolve family: {selection.family_id}"
            )
        family = get_tile_family(selection.family_id)
        normalized = family.selection(selection.orientation_id, selection.preset_id)
        preset = family.preset(normalized.preset_id)
        return ResolvedTileSystem(
            family.id, family.display_name, preset.id, normalized.orientation_id,
            PhysicalTileDimension(preset.dimension_kind, preset.primary_dimension_mm),
            self.profile_id,
        )

    def resolve_legacy_hex(
        self, dimension_mm: float, orientation: str | None,
    ) -> ResolvedTileSystem:
        canonical = {
            None: "point_top", "pointy": "point_top", "point_top": "point_top",
            "flat": "flat_top", "flat_top": "flat_top",
        }.get(orientation)
        if canonical is None:
            raise ValueError(f"Unsupported fabrication tile orientation: {orientation}")
        family = get_tile_family(self.family_id)
        family.normalize_orientation(canonical)
        return ResolvedTileSystem(
            family.id, family.display_name, None, canonical,
            PhysicalTileDimension("flat_to_flat", dimension_mm), self.profile_id,
        )

    def manufacturing_perimeter_bounds(self, model):
        from .mesh import _hexagon_fabrication_perimeter_bounds
        return _hexagon_fabrication_perimeter_bounds(model)


HEXAGON_FABRICATION_STRATEGY = HexagonFabricationStrategy()
_FABRICATION_STRATEGIES = MappingProxyType({
    HEXAGON_FABRICATION_STRATEGY.family_id: HEXAGON_FABRICATION_STRATEGY,
})


def get_tile_fabrication_strategy(family_id: str):
    try:
        return _FABRICATION_STRATEGIES[family_id]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported production fabrication family: {family_id}"
        ) from exc

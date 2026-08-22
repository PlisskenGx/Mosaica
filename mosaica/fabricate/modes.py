from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FabricationMode(str, Enum):
    STUDIO = "studio"
    MUSEUM = "museum"


@dataclass(frozen=True)
class FabricationModeDefinition:
    mode: FabricationMode
    display_name: str
    safe_envelope_mm: tuple[float, float]
    prime_tower_enabled: bool
    brim_intent: str
    ironing_enabled: bool
    adaptive_variable_layer_height_enabled: bool
    quality_tradeoff: str
    print_guide_instructions: tuple[tuple[str, str, str, str], ...]

    @property
    def mode_id(self) -> str:
        return self.mode.value

    def process_intent(self) -> dict[str, object]:
        prime_tower: dict[str, object] = {"enabled": self.prime_tower_enabled}
        brim: dict[str, object]
        if self.mode is FabricationMode.STUDIO:
            prime_tower["user_action"] = {
                "tab": "Others", "control": "Enable", "action": "Uncheck",
            }
            brim = {
                "type": "No Brim",
                "user_action": {
                    "tab": "Others", "control": "Brim type",
                    "action": "Set to No Brim",
                },
            }
        else:
            brim = {"recommendation": "Bambu default"}
        ironing: dict[str, object] = {"enabled": self.ironing_enabled}
        if self.ironing_enabled:
            ironing.update({
                "type": "Topmost surfaces", "pattern": "Concentric",
                "flow_percent": 18, "speed_mm_s": 30,
                "line_spacing_mm": 0.15,
            })
        return {
            "prime_tower": prime_tower,
            "brim": brim,
            "ironing": ironing,
            "adaptive_variable_layer_height": {
                "enabled": self.adaptive_variable_layer_height_enabled,
                "embedded_in_3mf": False,
            },
        }


STUDIO_MODE = FabricationModeDefinition(
    FabricationMode.STUDIO, "Studio", (228.0, 228.0), False, "No Brim", False, True,
    "Efficient production with fewer panels and a small risk of minor color transfer.",
    (
        ("Prime Tower", "Others", "Enable", "Uncheck"),
        ("Brim", "Others", "Brim type", "Set to No Brim"),
        ("Adaptive Variable Layer Height", "Quality", "Enable", "Enable"),
    ),
)

MUSEUM_MODE = FabricationModeDefinition(
    FabricationMode.MUSEUM, "Museum", (210.0, 210.0), True,
    "Bambu default", True, True,
    "Maximum finish quality and color purity with a Prime Tower and premium ironing.",
    (
        ("Prime Tower", "Others", "Enable", "Enable"),
        ("Brim", "Others", "Brim type", "Use Bambu default"),
        ("Adaptive Variable Layer Height", "Quality", "Enable", "Enable"),
        ("Ironing", "Quality", "Ironing type", "Apply premium ironing settings"),
    ),
)

FABRICATION_MODES = {
    FabricationMode.STUDIO: STUDIO_MODE,
    FabricationMode.MUSEUM: MUSEUM_MODE,
}


def resolve_fabrication_mode(
    mode: FabricationMode | str | None = None,
) -> FabricationModeDefinition:
    if mode is None:
        return STUDIO_MODE
    try:
        resolved = mode if isinstance(mode, FabricationMode) else FabricationMode(mode)
    except ValueError as error:
        choices = ", ".join(value.value for value in FabricationMode)
        raise ValueError(f"Fabrication mode must be one of: {choices}.") from error
    return FABRICATION_MODES[resolved]


def resolve_legacy_surface_finish(surface_finish: str) -> FabricationModeDefinition:
    try:
        return {"standard": STUDIO_MODE, "ironed": MUSEUM_MODE}[surface_finish]
    except KeyError as error:
        raise ValueError("Surface finish must be 'standard' or 'ironed'.") from error

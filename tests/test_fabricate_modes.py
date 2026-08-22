import pytest

from mosaica.fabricate.modes import (
    STUDIO_MODE, MUSEUM_MODE, FabricationMode, resolve_fabrication_mode,
)


def test_studio_mode_is_the_default_and_has_the_validated_process_contract():
    mode = resolve_fabrication_mode()
    assert mode is STUDIO_MODE
    assert mode.mode is FabricationMode.STUDIO
    assert mode.mode_id == "studio"
    assert mode.display_name == "Studio"
    assert not hasattr(FabricationMode, "FAST")
    assert mode.safe_envelope_mm == (228.0, 228.0)
    intent = mode.process_intent()
    assert intent["prime_tower"] == {
        "enabled": False,
        "user_action": {"tab": "Others", "control": "Enable", "action": "Uncheck"},
    }
    assert intent["brim"] == {
        "type": "No Brim",
        "user_action": {
            "tab": "Others", "control": "Brim type", "action": "Set to No Brim",
        },
    }
    assert intent["ironing"] == {"enabled": False}
    assert intent["adaptive_variable_layer_height"] == {
        "enabled": True, "embedded_in_3mf": False,
    }


def test_museum_mode_has_the_validated_premium_process_contract():
    mode = resolve_fabrication_mode("museum")
    assert mode is MUSEUM_MODE
    assert mode.mode is FabricationMode.MUSEUM
    assert mode.safe_envelope_mm == (210.0, 210.0)
    intent = mode.process_intent()
    assert intent["prime_tower"] == {"enabled": True}
    assert intent["brim"] == {"recommendation": "Bambu default"}
    assert "width_mm" not in intent["brim"]
    assert intent["ironing"] == {
        "enabled": True, "type": "Topmost surfaces", "pattern": "Concentric",
        "flow_percent": 18, "speed_mm_s": 30, "line_spacing_mm": 0.15,
    }
    assert intent["adaptive_variable_layer_height"] == {
        "enabled": True, "embedded_in_3mf": False,
    }


def test_unknown_fabrication_mode_is_rejected():
    with pytest.raises(ValueError, match="studio, museum"):
        resolve_fabrication_mode("draft")
    with pytest.raises(ValueError, match="studio, museum"):
        resolve_fabrication_mode("fast")

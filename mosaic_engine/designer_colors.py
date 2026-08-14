from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Iterable

from PIL import ImageColor


MAX_DESIGN_COLORS = 32

CURATED_MOSAICA_PALETTE = (
    ("Ivory", "#FAF9F6"), ("Black", "#000000"),
    ("Gray", "#808080"), ("Clay", "#B56F52"),
    ("Denim", "#466984"), ("Warm White", "#EEE9DF"),
    ("Sand", "#D8C9B5"), ("Charcoal", "#303033"),
    ("Slate", "#555A60"), ("Mist", "#B8B9B5"),
    ("Espresso", "#49362D"), ("Walnut", "#765543"),
    ("Camel", "#A8825E"),
    ("Brick", "#9B493D"), ("Garnet", "#762F3A"),
    ("Coral", "#C86A5A"), ("Terracotta", "#C4774D"),
    ("Ochre", "#B88935"), ("Gold", "#D1A84A"),
    ("Sage", "#829276"), ("Olive", "#687044"),
    ("Forest", "#315B46"), ("Moss", "#55745B"),
    ("Navy", "#263E59"),
    ("Sky", "#7EA3B5"), ("Teal", "#347577"),
    ("Sea Glass", "#6E9B91"), ("Plum", "#66445F"),
    ("Lavender", "#8B7694"), ("Rose", "#A85E70"),
    ("Blush", "#D09A9C"), ("Dusty Pink", "#B77B82"),
)

LEGACY_PAINT_SLOTS = (
    ("paint-slot-1", "project-color-1"),
    ("paint-slot-2", "project-color-2"),
    ("paint-slot-3", "project-color-3"),
    ("paint-slot-4", "project-color-4"),
    ("paint-slot-5", "project-color-5"),
)

# Import compatibility for integrations that referenced the transitional
# v1.9.1 slot table. New Designer state does not use Paint slots.
DEFAULT_PAINT_SLOTS = LEGACY_PAINT_SLOTS


@dataclass(frozen=True)
class DesignColor:
    color_id: str
    display_color: str
    name: str
    order: int
    origin: str = "project"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DesignColorCount:
    color_id: str
    display_color: str
    name: str
    count: int
    order: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DesignerColorResolution:
    """Stable project design colors, independent of manufacturing slots."""

    colors: tuple[DesignColor, ...]
    role_to_color_id: dict[str, str]

    def __post_init__(self) -> None:
        if not self.colors:
            raise ValueError("A Designer project requires at least one design color.")
        if len(self.colors) > MAX_DESIGN_COLORS:
            raise ValueError(
                "This project has reached the current 32-color limit. Remove "
                "an unused color or simplify the artwork before adding another."
            )
        color_ids = [value.color_id for value in self.colors]
        orders = [value.order for value in self.colors]
        normalized = [self.normalize(value.display_color) for value in self.colors]
        if len(color_ids) != len(set(color_ids)):
            raise ValueError("Project design color IDs must be unique.")
        if len(orders) != len(set(orders)):
            raise ValueError("Project design color order values must be unique.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Equivalent project design colors must share one ID.")
        unknown = set(self.role_to_color_id.values()) - set(color_ids)
        if unknown:
            raise ValueError(
                "Semantic roles reference unknown project colors: "
                + ", ".join(sorted(unknown))
            )

    @staticmethod
    def normalize(value: str | tuple[int, int, int]) -> str:
        rgb = value if isinstance(value, tuple) else ImageColor.getrgb(value)
        return "#%02X%02X%02X" % rgb

    def resolve(self, role: str) -> DesignColor:
        try:
            color_id = self.role_to_color_id[role]
        except KeyError as exc:
            raise ValueError(f"Unknown Designer color role: {role}") from exc
        return self.by_id(color_id)

    def by_id(self, color_id: str) -> DesignColor:
        try:
            return next(value for value in self.colors if value.color_id == color_id)
        except StopIteration as exc:
            raise ValueError(f"Unknown Designer project color ID: {color_id}") from exc

    def color_id_for_rgb(self, rgb: tuple[int, int, int]) -> str | None:
        normalized = self.normalize(rgb)
        return next(
            (
                value.color_id for value in self.colors
                if self.normalize(value.display_color) == normalized
            ),
            None,
        )

    def with_artwork_colors(
        self,
        colors: Iterable[tuple[int, int, int]],
    ) -> DesignerColorResolution:
        appended = list(self.colors)
        known = {
            self.normalize(value.display_color): value.color_id
            for value in appended
        }
        next_number = max(
            (
                int(value.color_id.rsplit("-", 1)[-1])
                for value in appended
                if value.color_id.startswith("project-color-")
                and value.color_id.rsplit("-", 1)[-1].isdigit()
            ),
            default=0,
        ) + 1
        next_order = max(value.order for value in appended) + 1
        for rgb in colors:
            normalized = self.normalize(rgb)
            if normalized in known:
                continue
            color_id = f"project-color-{next_number}"
            appended.append(DesignColor(
                color_id=color_id,
                display_color=normalized,
                name=f"Artwork {normalized}",
                order=next_order,
                origin="artwork",
            ))
            known[normalized] = color_id
            next_number += 1
            next_order += 1
        return DesignerColorResolution(tuple(appended), dict(self.role_to_color_id))

    def add_color(self, display_color: str, name: str | None = None) -> DesignerColorResolution:
        normalized = self.normalize(display_color)
        if self.color_id_for_rgb(ImageColor.getrgb(normalized)) is not None:
            raise ValueError("This color already exists in the project.")
        if len(self.colors) >= MAX_DESIGN_COLORS:
            raise ValueError(
                "This project has reached the current 32-color limit. Remove "
                "an unused color or simplify the artwork before adding another."
            )
        next_number = max(
            (
                int(value.color_id.rsplit("-", 1)[-1])
                for value in self.colors
                if value.color_id.startswith("project-color-")
                and value.color_id.rsplit("-", 1)[-1].isdigit()
            ),
            default=0,
        ) + 1
        next_order = max(value.order for value in self.colors) + 1
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name:
            custom_count = sum(value.origin == "manual" for value in self.colors) + 1
            clean_name = f"Custom {custom_count}"
        added = DesignColor(
            f"project-color-{next_number}", normalized, clean_name,
            next_order, "manual",
        )
        return DesignerColorResolution(
            (*self.colors, added), dict(self.role_to_color_id),
        )

    def update_color(
        self, color_id: str, *, display_color: str, name: str,
    ) -> DesignerColorResolution:
        current = self.by_id(color_id)
        if current.origin == "canonical":
            raise ValueError("Canonical Mosaica design colors cannot be edited.")
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name:
            raise ValueError("Color name cannot be empty.")
        normalized = self.normalize(display_color)
        duplicate = next((
            value for value in self.colors
            if value.color_id != color_id
            and self.normalize(value.display_color) == normalized
        ), None)
        if duplicate is not None:
            raise ValueError("This color already exists in the project.")
        updated = tuple(
            replace(value, display_color=normalized, name=clean_name)
            if value.color_id == color_id else value
            for value in self.colors
        )
        if current.display_color == normalized and current.name == clean_name:
            return self
        return DesignerColorResolution(updated, dict(self.role_to_color_id))

    def remove_color(self, color_id: str) -> DesignerColorResolution:
        current = self.by_id(color_id)
        if current.origin == "canonical":
            raise ValueError("Canonical Mosaica design colors cannot be removed.")
        if color_id in self.role_to_color_id.values():
            raise ValueError("This color is required by the project and cannot be removed.")
        remaining = tuple(value for value in self.colors if value.color_id != color_id)
        return DesignerColorResolution(remaining, dict(self.role_to_color_id))

    def count_visible(
        self,
        placements: Iterable[tuple[str, str]],
    ) -> tuple[DesignColorCount, ...]:
        return self.count_visible_color_ids(
            (piece_type, self.resolve(role).color_id)
            for piece_type, role in placements
        )

    def count_visible_color_ids(
        self,
        placements: Iterable[tuple[str, str]],
    ) -> tuple[DesignColorCount, ...]:
        counts = {value.color_id: 0 for value in self.colors}
        for piece_type, color_id in placements:
            if piece_type == "outside":
                continue
            try:
                counts[color_id] += 1
            except KeyError as exc:
                raise ValueError(
                    f"Unknown Designer project color ID: {color_id}"
                ) from exc
        return tuple(
            DesignColorCount(
                color_id=color.color_id,
                display_color=color.display_color,
                name=color.name,
                count=counts[color.color_id],
                order=color.order,
            )
            for color in sorted(self.colors, key=lambda value: value.order)
            if counts[color.color_id] > 0
        )

    def to_dict(self) -> dict:
        return {
            "design_color_safety_limit": MAX_DESIGN_COLORS,
            "design_colors": [
                value.to_dict()
                for value in sorted(self.colors, key=lambda color: color.order)
            ],
            "role_to_color_id": dict(sorted(self.role_to_color_id.items())),
            "manufacturing_mapping": None,
        }


CANONICAL_MOSAICA_COLORS = tuple(
    DesignColor(
        f"project-color-{index}", display_color, name, index - 1, "canonical",
    )
    for index, (name, display_color) in enumerate(
        CURATED_MOSAICA_PALETTE, start=1,
    )
)


DEFAULT_DESIGNER_COLORS = DesignerColorResolution(
    colors=CANONICAL_MOSAICA_COLORS,
    role_to_color_id={
        "background": "project-color-1",
        "edge": "project-color-1",
        "border_primary": "project-color-2",
        "border_secondary": "project-color-3",
    },
)


# Compatibility aliases for integrations importing the pre-v1.4.1 names.
PhysicalColor = DesignColor
PhysicalColorCount = DesignColorCount
MAX_ARTWORK_DESIGN_COLORS = MAX_DESIGN_COLORS

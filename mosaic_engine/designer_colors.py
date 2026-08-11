from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .border import MAX_PROJECT_COLORS


@dataclass(frozen=True)
class PhysicalColor:
    color_id: str
    display_color: str
    name: str
    order: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PhysicalColorCount:
    color_id: str
    display_color: str
    name: str
    count: int
    order: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DesignerColorResolution:
    """Resolve semantic design roles through one ordered physical palette."""

    colors: tuple[PhysicalColor, ...]
    role_to_color_id: dict[str, str]

    def __post_init__(self) -> None:
        if not self.colors:
            raise ValueError("A Designer project requires at least one physical color.")
        if len(self.colors) > MAX_PROJECT_COLORS:
            raise ValueError(
                f"Designer projects support at most {MAX_PROJECT_COLORS} physical colors."
            )
        color_ids = [value.color_id for value in self.colors]
        orders = [value.order for value in self.colors]
        if len(color_ids) != len(set(color_ids)):
            raise ValueError("Physical color IDs must be unique.")
        if len(orders) != len(set(orders)):
            raise ValueError("Physical color order values must be unique.")
        unknown = set(self.role_to_color_id.values()) - set(color_ids)
        if unknown:
            raise ValueError(
                "Semantic roles reference unknown physical colors: "
                + ", ".join(sorted(unknown))
            )

    def resolve(self, role: str) -> PhysicalColor:
        try:
            color_id = self.role_to_color_id[role]
        except KeyError as exc:
            raise ValueError(f"Unknown Designer color role: {role}") from exc
        return next(value for value in self.colors if value.color_id == color_id)

    def count_visible(
        self,
        placements: Iterable[tuple[str, str]],
    ) -> tuple[PhysicalColorCount, ...]:
        """Count visible pieces by resolved physical color, never by role."""

        return self.count_visible_color_ids(
            (piece_type, self.resolve(role).color_id)
            for piece_type, role in placements
        )

    def count_visible_color_ids(
        self,
        placements: Iterable[tuple[str, str]],
    ) -> tuple[PhysicalColorCount, ...]:
        """Count the authoritative effective physical IDs for visible pieces."""

        counts = {value.color_id: 0 for value in self.colors}
        for piece_type, color_id in placements:
            if piece_type == "outside":
                continue
            try:
                counts[color_id] += 1
            except KeyError as exc:
                raise ValueError(f"Unknown Designer physical color ID: {color_id}") from exc
        return tuple(
            PhysicalColorCount(
                color_id=color.color_id,
                display_color=color.display_color,
                name=color.name,
                count=counts[color.color_id],
                order=color.order,
            )
            for color in sorted(self.colors, key=lambda value: value.order)
            if counts[color.color_id] > 0
        )

    def with_physical_colors(
        self,
        colors: tuple[PhysicalColor, ...],
    ) -> DesignerColorResolution:
        """Replace slot metadata while preserving stable IDs and role mappings."""

        if {value.color_id for value in colors} != {
            value.color_id for value in self.colors
        }:
            raise ValueError("Designer physical-color replacements must preserve IDs.")
        return DesignerColorResolution(colors, dict(self.role_to_color_id))

    def to_dict(self) -> dict:
        return {
            "maximum_project_colors": MAX_PROJECT_COLORS,
            "physical_colors": [
                value.to_dict()
                for value in sorted(self.colors, key=lambda color: color.order)
            ],
            "role_to_color_id": dict(sorted(self.role_to_color_id.items())),
            "shared_project_palette": True,
        }


DEFAULT_DESIGNER_COLORS = DesignerColorResolution(
    colors=(
        PhysicalColor("color-1", "#FAF9F6", "Ivory", 0),
        PhysicalColor("color-2", "#D8D6CF", "Edge Gray", 1),
        PhysicalColor("color-3", "#34373D", "Charcoal", 2),
        PhysicalColor("color-4", "#A87655", "Clay", 3),
    ),
    role_to_color_id={
        "background": "color-1",
        "edge": "color-2",
        "border_primary": "color-3",
        "border_secondary": "color-4",
    },
)

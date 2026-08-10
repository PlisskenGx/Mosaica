"""
Mosaic Engine.

Standalone physical tile mosaic generation engine.
"""

from .engine import generate_mosaic

from .geometry import (
    GridGeometry,
    TilePlacement,
    build_geometry,
)

from .model import (
    MosaicConfig,
    MosaicResult,
    PaletteColor,
)

from .processing import (
    cleanup_grid,
    luminance,
    threshold_grid,
)

from .project import (
    MosaicProject,
    PROJECT_SCHEMA_VERSION,
)


__all__ = [
    "generate_mosaic",

    "build_geometry",
    "GridGeometry",
    "TilePlacement",

    "MosaicConfig",
    "MosaicResult",
    "PaletteColor",

    "cleanup_grid",
    "luminance",
    "threshold_grid",

    "MosaicProject",
    "PROJECT_SCHEMA_VERSION",
]


__version__ = "0.6.3"

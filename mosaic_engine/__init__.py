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
from .benchmark import (
    BenchmarkReport,
    CorrectionRegion,
    ProposalOverlapReport,
    analyze_benchmark_projects,
    analyze_project,
    benchmark_reports_json,
    connected_correction_regions,
    evaluate_refinement_proposals,
    format_benchmark_reports,
)
from .evidence import (
    BWEvidence,
    TileEvidence,
    compute_bw_evidence,
    compute_project_bw_evidence,
    physical_neighbor_rings,
)
from .refinement import (
    CandidateRegion,
    RefinementProposal,
    RefinementReport,
    ScoreBreakdown,
    TileChange,
    format_refinement_report,
    generate_refinement_proposals,
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
    "BenchmarkReport",
    "CorrectionRegion",
    "ProposalOverlapReport",
    "analyze_benchmark_projects",
    "analyze_project",
    "benchmark_reports_json",
    "connected_correction_regions",
    "evaluate_refinement_proposals",
    "format_benchmark_reports",
    "BWEvidence",
    "TileEvidence",
    "compute_bw_evidence",
    "compute_project_bw_evidence",
    "physical_neighbor_rings",
    "CandidateRegion",
    "RefinementProposal",
    "RefinementReport",
    "ScoreBreakdown",
    "TileChange",
    "format_refinement_report",
    "generate_refinement_proposals",
    "luminance",
    "threshold_grid",

    "MosaicProject",
    "PROJECT_SCHEMA_VERSION",
]


__version__ = "0.6.3"

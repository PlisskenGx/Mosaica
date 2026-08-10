"""
Mosaic Engine.

Standalone physical tile mosaic generation engine.
"""

from .engine import generate_mosaic
from .contour_refinement import (
    ContourAlternative,
    ContourCandidate,
    ContourChange,
    ContourRefinementReport,
    ContourScore,
    format_contour_refinement_report,
    generate_contour_refinement_proposals,
)

from .geometry import (
    GridGeometry,
    TilePlacement,
    build_geometry,
)
from .fabrication import (
    BuildGuidePiece,
    CutPieceRecord,
    FabricationData,
    MaterialRecord,
    build_fabrication_data,
    export_assembly_map_svg,
    export_cut_piece_schedule_csv,
    export_fabrication_package,
    export_material_schedule_csv,
    export_project_summary,
    export_row_build_guide_csv,
    export_row_build_guide_text,
)
from .print_parts import (
    INCH_TO_MM,
    PlacementPart,
    PrintablePart,
    PrintPartsManifest,
    build_print_parts_manifest,
    calibration_polygon,
    export_calibration_package,
    export_print_parts_package,
    inches_to_mm,
    offset_polygon,
    triangulate_extrusion,
    write_ascii_stl,
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
    evaluate_contour_refinement_proposals,
    format_benchmark_reports,
)
from .evidence import (
    BWEvidence,
    BWEvidenceCache,
    TileEvidence,
    build_evidence_cache,
    cache_project_bw_evidence,
    compute_bw_evidence,
    compute_project_bw_evidence,
    evidence_cache_validity,
    evidence_input_fingerprint,
    physical_neighbor_rings,
    resolve_project_bw_evidence,
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
    "ContourAlternative",
    "ContourCandidate",
    "ContourChange",
    "ContourRefinementReport",
    "ContourScore",
    "format_contour_refinement_report",
    "generate_contour_refinement_proposals",

    "build_geometry",
    "GridGeometry",
    "TilePlacement",
    "BuildGuidePiece",
    "CutPieceRecord",
    "FabricationData",
    "MaterialRecord",
    "build_fabrication_data",
    "export_assembly_map_svg",
    "export_cut_piece_schedule_csv",
    "export_fabrication_package",
    "export_material_schedule_csv",
    "export_project_summary",
    "export_row_build_guide_csv",
    "export_row_build_guide_text",
    "INCH_TO_MM",
    "PlacementPart",
    "PrintablePart",
    "PrintPartsManifest",
    "build_print_parts_manifest",
    "calibration_polygon",
    "export_calibration_package",
    "export_print_parts_package",
    "inches_to_mm",
    "offset_polygon",
    "triangulate_extrusion",
    "write_ascii_stl",

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
    "evaluate_contour_refinement_proposals",
    "format_benchmark_reports",
    "BWEvidence",
    "BWEvidenceCache",
    "TileEvidence",
    "build_evidence_cache",
    "cache_project_bw_evidence",
    "compute_bw_evidence",
    "compute_project_bw_evidence",
    "evidence_cache_validity",
    "evidence_input_fingerprint",
    "physical_neighbor_rings",
    "resolve_project_bw_evidence",
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

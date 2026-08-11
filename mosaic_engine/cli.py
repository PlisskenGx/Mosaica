from __future__ import annotations

import argparse
import json

from email import parser
from pathlib import Path

from .benchmark import (
    analyze_benchmark_projects,
    benchmark_reports_json,
    evaluate_refinement_proposals,
    format_benchmark_reports,
)
from .evidence import (
    cache_project_bw_evidence,
    resolve_project_bw_evidence,
)
from .engine import generate_mosaic

from .export import (
    export_counts_csv,
    export_grid_csv,
    export_placements_csv,
    export_preview_png,
)
from .fabrication import export_fabrication_package
from .print_parts import (
    export_calibration_package,
    export_print_parts_package,
)

from .model import (
    MosaicConfig,
    PaletteColor,
)
from .project import MosaicProject
from .refinement import (
    format_refinement_report,
    generate_refinement_proposals,
)


def _parse_color(
    spec: str,
) -> PaletteColor:

    """
    Parse:

        NAME:#RRGGBB

    or:

        NAME:#RRGGBB:SKU
    """

    parts = spec.split(":")

    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "Color must be "
            "NAME:#RRGGBB[:SKU]"
        )

    name = parts[0]

    hx = (
        parts[1]
        .lstrip("#")
    )

    if len(hx) != 6:
        raise argparse.ArgumentTypeError(
            "Hex color must be 6 digits"
        )

    try:
        rgb = tuple(
            int(
                hx[i:i + 2],
                16,
            )
            for i in (
                0,
                2,
                4,
            )
        )

    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Invalid hexadecimal color."
        ) from exc

    sku = (
        parts[2]
        if len(parts) > 2
        else None
    )

    return PaletteColor(
        name=name,
        rgb=rgb,
        sku=sku,
    )


def _parse_coordinate(spec: str) -> tuple[int, int]:
    try:
        row, column = (
            int(value)
            for value in spec.split(",")
        )
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Tile coordinates must be ROW,COLUMN."
        ) from exc

    if row < 1 or column < 1:
        raise argparse.ArgumentTypeError(
            "CLI tile coordinates are one-based."
        )

    return row - 1, column - 1


def _parse_override(spec: str) -> tuple[int, int, int]:
    try:
        coordinate, palette = spec.split(":", 1)
        row, column = _parse_coordinate(coordinate)
        palette_index = int(palette)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Override must be ROW,COLUMN:PALETTE_INDEX."
        ) from exc

    if palette_index < 1:
        raise argparse.ArgumentTypeError(
            "CLI palette indices are one-based."
        )

    return row, column, palette_index - 1


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Generate a physical tile mosaic "
            "from an image"
        )
    )

    parser.add_argument(
        "source",
        nargs="?",
        help="source image",
    )

    parser.add_argument(
        "--color",
        action="append",
        type=_parse_color,
        help=(
            "palette color as "
            "NAME:#RRGGBB[:SKU]"
        ),
    )

    parser.add_argument(
        "--shape",
        choices=[
            "square",
            "hex",
        ],
        default="square",
    )

    parser.add_argument(
        "--tile",
        type=float,
        default=1.0,

        help=(
            "tile size in inches; "
            "for hex, this is across-flats"
        ),
    )

    parser.add_argument(
        "--tile-height",
        type=float,

        help=(
            "square/rectangular tile height; "
            "defaults to --tile"
        ),
    )

    parser.add_argument(
        "--hex-orientation",
        choices=[
            "pointy",
            "flat",
        ],
        default="pointy",
    )

    parser.add_argument(
        "--grout",
        type=float,
        default=0.0,

        help=(
            "clear grout gap in inches"
        ),
    )

    parser.add_argument(
        "--width",
        type=float,
    )

    parser.add_argument(
        "--height",
        type=float,
    )

    parser.add_argument(
        "--cols",
        type=int,
    )

    parser.add_argument(
        "--rows",
        type=int,
    )

    parser.add_argument(
        "--fit",
        choices=[
            "contain",
            "cover",
            "stretch",
        ],
        default="contain",
    )

    parser.add_argument(
        "--mode",
        choices=[
            "palette",
            "bw",
        ],
        default="palette",

        help=(
            "color interpretation mode"
        ),
    )

    parser.add_argument(
        "--threshold",
        type=int,
        default=128,

        help=(
            "black/white luminance threshold "
            "from 0-255"
        ),
    )

    parser.add_argument(
        "--invert",
        action="store_true",

        help=(
            "invert foreground/background "
            "in black/white mode"
        ),
    )

    parser.add_argument(
        "--coverage-threshold",
        type=float,
        default=0.45,

        help=(
            "minimum foreground area fraction "
            "for black/white tile classification"
        ),
    )

    parser.add_argument(
        "--cleanup",
        type=int,
        default=0,

        help=(
            "number of topology-aware "
            "cleanup passes"
        ),
    )

    parser.add_argument(
        "--ppi",
        type=int,
        default=48,

        help=(
            "preview pixels per physical inch"
        ),
    )

    parser.add_argument(
        "--out",
        default="mosaic_output",
    )

    parser.add_argument(
        "--save-project",
        help="save editable project state as JSON",
    )

    parser.add_argument(
        "--load-project",
        help="load editable project state from JSON",
    )

    parser.add_argument(
        "--set-override",
        action="append",
        type=_parse_override,
        default=[],
        metavar="ROW,COLUMN:PALETTE_INDEX",
        help="set a one-based tile override",
    )

    parser.add_argument(
        "--clear-override",
        action="append",
        type=_parse_coordinate,
        default=[],
        metavar="ROW,COLUMN",
        help="clear a one-based tile override",
    )

    parser.add_argument(
        "--clear-all-overrides",
        action="store_true",
        help="clear every manual override",
    )

    parser.add_argument(
        "--edit-project",
        help="open a saved MosaicProject in the local editor",
    )

    parser.add_argument(
        "--designer",
        action="store_true",
        help="launch the Mosaic Designer preset workflow",
    )

    parser.add_argument(
        "--editor-port",
        type=int,
        default=8765,
        help="localhost port for the project editor or Mosaic Designer",
    )

    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="start the editor without opening a browser",
    )

    parser.add_argument(
        "--benchmark",
        action="append",
        metavar="PROJECT_JSON",
        help=(
            "analyze a saved benchmark project; repeat for multiple projects"
        ),
    )

    parser.add_argument(
        "--benchmark-json",
        action="store_true",
        help="emit benchmark reports as JSON",
    )

    parser.add_argument(
        "--benchmark-source-evidence",
        action="store_true",
        help="include source-derived evidence; requires source artwork",
    )

    parser.add_argument(
        "--refine-proposals",
        metavar="PROJECT_JSON",
        help="report deterministic refinement proposals for a saved project",
    )

    parser.add_argument(
        "--refine-json",
        action="store_true",
        help="emit refinement proposals as JSON",
    )

    parser.add_argument(
        "--cache-evidence",
        metavar="PROJECT_JSON",
        help="compute and persist deterministic BW evidence for a project",
    )

    parser.add_argument(
        "--fabrication",
        metavar="PROJECT_JSON",
        help="export fabrication-ready schedules and assembly map",
    )

    parser.add_argument(
        "--waste",
        type=float,
        default=0.10,
        help="fabrication purchase waste factor; default 0.10",
    )

    parser.add_argument(
        "--print-parts",
        metavar="PROJECT_JSON",
        help="export deduplicated printable STL parts from a saved project",
    )

    parser.add_argument(
        "--print-calibration",
        action="store_true",
        help="export an XY-compensation calibration set",
    )

    parser.add_argument(
        "--thickness-mm",
        type=float,
        default=3.0,
        help="printed tile thickness in millimeters; default 3.0",
    )

    parser.add_argument(
        "--xy-offset-mm",
        type=float,
        default=0.0,
        help="uniform printable-outline compensation in millimeters",
    )

    parser.add_argument(
        "--art-inset",
        type=float,
        default=0.0,

        help=(
            "reserved inset around the artwork "
            "in inches"
        ),
    )

    parser.add_argument(
        "--art-scale",
        type=float,
        default=1.0,

        help=(
            "artwork scale inside its available "
            "region"
        ),
    )

    parser.add_argument(
        "--art-offset-x",
        type=float,
        default=0.0,

        help="horizontal artwork offset in inches",
    )

    parser.add_argument(
        "--art-offset-y",
        type=float,
        default=0.0,

        help="vertical artwork offset in inches",
    )

    args = parser.parse_args()

    if args.designer:
        if (
            args.source
            or args.color
            or args.load_project
            or args.edit_project
            or args.benchmark
            or args.refine_proposals
            or args.cache_evidence
            or args.fabrication
            or args.print_parts
            or args.print_calibration
            or args.set_override
            or args.clear_override
            or args.clear_all_overrides
            or args.save_project
        ):
            parser.error(
                "generation, project editing, export, benchmark, refinement, "
                "and evidence options cannot be used with --designer"
            )
        if not 1 <= args.editor_port <= 65535:
            parser.error("--editor-port must be between 1 and 65535")
        from .designer import run_designer
        run_designer(
            port=args.editor_port,
            open_browser=not args.no_browser,
        )
        return

    if args.print_parts or args.print_calibration:
        if args.print_parts and args.print_calibration:
            parser.error("--print-parts and --print-calibration are mutually exclusive")
        if (
            args.source
            or args.color
            or args.load_project
            or args.edit_project
            or args.benchmark
            or args.refine_proposals
            or args.cache_evidence
            or args.fabrication
            or args.set_override
            or args.clear_override
            or args.clear_all_overrides
            or args.save_project
        ):
            parser.error(
                "generation, editing, fabrication, benchmark, refinement, and "
                "evidence options cannot be used with print output"
            )
        if args.thickness_mm <= 0:
            parser.error("--thickness-mm must be positive")
        try:
            if args.print_calibration:
                paths = export_calibration_package(
                    args.out,
                    shape=args.shape,
                    tile_size_in=args.tile,
                    thickness_mm=args.thickness_mm,
                )
                label = "Print calibration output"
            else:
                project = MosaicProject.load(args.print_parts)
                generated_before = project.generated_grid
                overrides_before = project.overrides
                paths = export_print_parts_package(
                    project,
                    args.out,
                    thickness_mm=args.thickness_mm,
                    xy_offset_mm=args.xy_offset_mm,
                )
                if (
                    project.generated_grid != generated_before
                    or project.overrides != overrides_before
                ):
                    raise RuntimeError(
                        "Print-parts export unexpectedly changed project tile state."
                    )
                label = "Printable parts output"
        except ValueError as exc:
            parser.error(str(exc))
        print(f"{label}: {Path(args.out).resolve()}")
        for name, path in paths.items():
            print(f"  {name}: {path}")
        return

    if args.fabrication:
        if (
            args.source
            or args.color
            or args.load_project
            or args.edit_project
            or args.benchmark
            or args.refine_proposals
            or args.cache_evidence
            or args.set_override
            or args.clear_override
            or args.clear_all_overrides
            or args.save_project
        ):
            parser.error(
                "generation, editing, benchmark, refinement, and evidence "
                "options cannot be used with --fabrication"
            )
        if args.waste < 0:
            parser.error("--waste cannot be negative")
        project = MosaicProject.load(args.fabrication)
        generated_before = project.generated_grid
        overrides_before = project.overrides
        paths = export_fabrication_package(
            project,
            args.out,
            waste_factor=args.waste,
        )
        if (
            project.generated_grid != generated_before
            or project.overrides != overrides_before
        ):
            raise RuntimeError(
                "Fabrication export unexpectedly changed project tile state."
            )
        print(f"Fabrication output: {Path(args.out).resolve()}")
        for name, path in paths.items():
            print(f"  {name}: {path}")
        return

    if args.cache_evidence:
        if (
            args.source
            or args.color
            or args.load_project
            or args.edit_project
            or args.benchmark
            or args.refine_proposals
            or args.refine_json
            or args.benchmark_json
            or args.benchmark_source_evidence
            or args.set_override
            or args.clear_override
            or args.clear_all_overrides
            or args.save_project
        ):
            parser.error(
                "generation, editing, benchmark, and refinement options "
                "cannot be used with --cache-evidence"
            )
        project_path = Path(args.cache_evidence)
        project = MosaicProject.load(project_path)
        generated_before = project.generated_grid
        overrides_before = project.overrides
        cache_project_bw_evidence(project)
        if (
            project.generated_grid != generated_before
            or project.overrides != overrides_before
        ):
            raise RuntimeError("Evidence caching unexpectedly changed tile state.")
        project.save(project_path)
        print(f"Cached BW evidence: {project_path}")
        return

    if args.refine_proposals:
        if (
            args.source
            or args.color
            or args.load_project
            or args.edit_project
            or args.benchmark
        ):
            parser.error(
                "source, --color, --load-project, --edit-project, and "
                "--benchmark cannot be used with --refine-proposals"
            )
        project = MosaicProject.load(args.refine_proposals)
        evidence = resolve_project_bw_evidence(project)
        refinement = generate_refinement_proposals(project, evidence)
        evaluation = evaluate_refinement_proposals(refinement, project)
        if args.refine_json:
            print(json.dumps({
                "refinement": refinement.to_dict(),
                "evaluation": evaluation.to_dict(),
            }, indent=2, sort_keys=True))
        else:
            print(format_refinement_report(refinement))
            print("")
            print(
                "Benchmark overlap: "
                f"{evaluation.proposed_matching_human_direction}/"
                f"{evaluation.proposed_changes} proposed changes match; "
                f"{evaluation.human_changes_captured}/"
                f"{evaluation.human_real_changes} human changes captured"
            )
        return

    if args.refine_json:
        parser.error("--refine-json requires --refine-proposals")

    if args.benchmark:
        if args.source or args.color or args.load_project or args.edit_project:
            parser.error(
                "source, --color, --load-project, and --edit-project "
                "cannot be used with --benchmark"
            )
        reports = analyze_benchmark_projects(
            args.benchmark,
            compute_source_evidence=args.benchmark_source_evidence,
        )
        print(
            benchmark_reports_json(reports)
            if args.benchmark_json
            else format_benchmark_reports(reports)
        )
        return

    if args.benchmark_json or args.benchmark_source_evidence:
        parser.error(
            "--benchmark-json and --benchmark-source-evidence require "
            "--benchmark"
        )

    if args.edit_project:
        if args.source or args.color or args.load_project:
            parser.error(
                "source, --color, and --load-project cannot be "
                "used with --edit-project"
            )

        if not 1 <= args.editor_port <= 65535:
            parser.error("--editor-port must be between 1 and 65535")

        from .editor import run_editor

        run_editor(
            args.edit_project,
            port=args.editor_port,
            open_browser=not args.no_browser,
        )
        return

    if not 0 <= args.threshold <= 255:
        parser.error(
            "--threshold must be "
            "between 0 and 255"
        )

    if args.cleanup < 0:
        parser.error(
            "--cleanup cannot be negative"
        )

    if args.ppi <= 0:
        parser.error(
            "--ppi must be positive"
        )

    if not 0 < args.coverage_threshold <= 1:
        parser.error(
            "--coverage-threshold must be greater "
            "than 0 and at most 1"
        )

    if args.load_project:
        if args.source or args.color:
            parser.error(
                "source and --color cannot be used with --load-project"
            )

        project = MosaicProject.load(args.load_project)
        config = project.config

    else:
        if not args.source:
            parser.error(
                "source is required unless --load-project is used"
            )

        if not args.color:
            parser.error(
                "at least one --color is required for generation"
            )

        config = MosaicConfig(

            tile_shape=args.shape,

            tile_width_in=args.tile,

            tile_height_in=(
                args.tile_height
                if args.tile_height
                is not None
                else args.tile
            ),

            grout_width_in=args.grout,

            hex_orientation=(
                args.hex_orientation
            ),

            target_width_in=args.width,
            target_height_in=args.height,

            columns=args.cols,
            rows=args.rows,

            fit=args.fit,

            quantization_mode=args.mode,

            bw_threshold=args.threshold,

            coverage_threshold=(
                args.coverage_threshold
            ),

            invert_bw=args.invert,

            cleanup_passes=(
                args.cleanup
            ),
            artwork_inset_in=args.art_inset,

            artwork_scale=args.art_scale,

            artwork_offset_x_in=(
                args.art_offset_x
            ),

            artwork_offset_y_in=(
                args.art_offset_y
            ),
        )

        result = generate_mosaic(
            source=args.source,
            palette=args.color,
            config=config,
        )
        project = MosaicProject.from_result(result)

    if args.clear_all_overrides:
        project.clear_all_overrides()

    try:
        for row, column in args.clear_override:
            project.clear_override(row, column)

        for row, column, palette_index in args.set_override:
            project.set_override(row, column, palette_index)
    except (IndexError, ValueError) as exc:
        parser.error(str(exc))

    if args.save_project:
        project.save(args.save_project)

    result = project

    out = Path(
        args.out
    )

    out.mkdir(
        parents=True,
        exist_ok=True,
    )

    export_preview_png(
        result,

        out / "preview.png",

        pixels_per_inch=(
            args.ppi
        ),
    )

    export_counts_csv(
        result,
        out / "counts.csv",
    )

    export_grid_csv(
        result,
        out / "grid.csv",
    )

    export_placements_csv(
        result,
        out / "placements.csv",
    )

    print(
        f"Shape: "
        f"{config.tile_shape}"
    )

    if (
        config.tile_shape
        == "hex"
    ):
        print(
            "Hex orientation: "
            f"{config.hex_orientation}"
        )

        print(
            "Hex across-flats: "
            f"{config.tile_width_in:.4f} in"
        )

    print(
        "Interpretation: "
        f"{config.quantization_mode}"
    )

    if (
        config.quantization_mode
        == "bw"
    ):
        print(
            "Threshold: "
            f"{config.bw_threshold}"
        )

        print(
            "Inverted: "
            f"{config.invert_bw}"
        )

    print(
        "Cleanup passes: "
        f"{config.cleanup_passes}"
    )

    print(
        f"Grid: "
        f"{result.columns} "
        f"x "
        f"{result.rows}"
    )

    print(
        "Physical size: "
        f"{result.physical_width_in:.3f} "
        "x "
        f"{result.physical_height_in:.3f} "
        "in"
    )

    print(
        "Tiles: "
        f"{result.columns * result.rows}"
    )

    print(
        result.counts()
    )


if __name__ == "__main__":
    main()

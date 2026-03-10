from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "export":
        return _run_export(args)
    if args.command == "apply":
        return _run_apply(args)

    parser.print_help()
    return 1


def export_main() -> int:
    parser = _build_export_parser()
    args = parser.parse_args()
    return _run_export(args)


def apply_main() -> int:
    parser = _build_apply_parser()
    args = parser.parse_args()
    return _run_apply(args)


def _run_export(args: argparse.Namespace) -> int:
    try:
        from . import workflow
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = workflow.export_jobs(
            input_pdf=Path(args.input_pdf),
            job_dir=Path(args.job_dir),
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            pages=args.pages,
            rpc_doclayout=args.rpc_doclayout,
            skip_scanned_detection=args.skip_scanned_detection,
            debug=args.debug,
        )
    except workflow.JobPackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _run_apply(args: argparse.Namespace) -> int:
    if args.no_dual and args.no_mono:
        print("error: --no-dual and --no-mono cannot both be enabled", file=sys.stderr)
        return 2
    try:
        from . import workflow
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = workflow.apply_jobs(
            job_dir=Path(args.job_dir),
            translations_file=Path(args.translations),
            output_dir=Path(args.output_dir),
            no_dual=args.no_dual,
            no_mono=args.no_mono,
            debug=args.debug,
            fail_on_missing=not args.allow_missing,
        )
    except workflow.JobPackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="babeldoc-jobpack",
        description="BabelDOC job package export/apply workflow.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True
    subparsers.add_parser("export", parents=[_build_export_parser()], add_help=False)
    subparsers.add_parser("apply", parents=[_build_apply_parser()], add_help=False)
    return parser


def _build_export_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="babeldoc-export-jobs",
        description="Export a PDF into a BabelDOC job package.",
    )
    parser.add_argument("input_pdf", help="Path to input PDF.")
    parser.add_argument("--job-dir", required=True, help="Output directory for job pack.")
    parser.add_argument("--lang-in", default="en", help="Source language code.")
    parser.add_argument("--lang-out", default="zh", help="Target language code.")
    parser.add_argument("--pages", help="Page filter string, for example: 1,2,5-7")
    parser.add_argument("--rpc-doclayout", help="Optional rpc_doclayout host.")
    parser.add_argument(
        "--skip-scanned-detection",
        action="store_true",
        default=True,
        help="Skip scanned-file detection (default: true).",
    )
    parser.add_argument(
        "--detect-scanned",
        dest="skip_scanned_detection",
        action="store_false",
        help="Enable scanned-file detection.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable BabelDOC debug mode in the underlying pipeline.",
    )
    return parser


def _build_apply_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="babeldoc-apply-jobs",
        description="Apply translated text to a BabelDOC job package and render output PDF.",
    )
    parser.add_argument("job_dir", help="Path to job pack directory.")
    parser.add_argument(
        "--translations",
        required=True,
        help="Path to translations JSON. Supports {id:text} or [{id,translated_text}]",
    )
    parser.add_argument("--output-dir", required=True, help="Directory for output PDF(s).")
    parser.add_argument("--no-dual", action="store_true", help="Disable dual-language output.")
    parser.add_argument("--no-mono", action="store_true", help="Disable monolingual output.")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow missing translations and fallback to source text.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable BabelDOC debug mode in the apply phase.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

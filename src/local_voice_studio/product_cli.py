"""Public app/CLI entrypoints, independent of channel episode layouts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .core import StudioError, TimingOverflow


def parser():
    root = argparse.ArgumentParser(description="Produce voiced video or run the local upload/API service")
    commands = root.add_subparsers(dest="command", required=True)
    process = commands.add_parser("process", help="Video + narration text or SRT/VTT -> voiced MP4")
    process.add_argument("--video", type=Path, required=True)
    source = process.add_mutually_exclusive_group(required=True)
    source.add_argument("--narration", type=Path, help="UTF-8 narration text file")
    source.add_argument("--text", help="Literal narration text")
    source.add_argument("--subtitles", type=Path, help="Plain-text SRT or VTT, used for speech and burned captions")
    process.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2]/"config/explainer.toml")
    process.add_argument("--output-dir", type=Path, required=True)
    process.add_argument("--reference", type=Path, help="Sample audio for a configured cloning provider")
    process.add_argument("--reference-text", help="Exact transcript of the sample")
    process.add_argument("--dry-run", action="store_true")
    serve = commands.add_parser("serve", help="Start local upload page, HTTP API and OpenAPI docs")
    serve.add_argument("--profile", action="append", metavar="NAME=CONFIG", help="Expose a named voice profile; repeatable")
    serve.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[2]/"outputs/service")
    serve.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--allow-origin", action="append", default=[], help="Explicit browser app origin allowed to call this local API")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "process":
            from .product import produce_video
            narration = args.narration.read_text(encoding="utf-8-sig") if args.narration else args.text
            result = produce_video(args.video, args.config, args.output_dir, narration=narration,
                                   subtitles=args.subtitles, reference_audio=args.reference,
                                   reference_text=args.reference_text, dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
            return 0
        profiles = {}
        for item in args.profile or [f"default={Path(__file__).resolve().parents[2]/'config/explainer.toml'}"]:
            name, separator, path = item.partition("=")
            if not separator or not name or name in profiles:
                raise StudioError("Each --profile must be a unique NAME=CONFIG")
            profiles[name] = Path(path).expanduser().resolve()
        try:
            import uvicorn
            from .api import create_app
        except ImportError as exc:
            raise StudioError("Install this repository's service extra/setup-service.sh in its own runtime") from exc
        if not 1 <= args.port <= 65535:
            raise StudioError("Port must be between 1 and 65535")
        app = create_app(profiles, args.data_dir.expanduser().resolve(), allowed_origins=args.allow_origin)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    except (StudioError, OSError, ValueError) as exc:
        status = "needs_retime" if isinstance(exc, TimingOverflow) else "blocked"
        result = {"status": status, "error": str(exc)}
        if isinstance(exc, TimingOverflow):
            result["retime_plan"] = exc.details
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 3 if status == "needs_retime" else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Isolate optional model stacks while retaining one product/API process."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys

from .core import StudioError, TimingOverflow, sha256


def status():
    modules = {}
    for name in ("mlx_audio", "mlx", "numpy", "soundfile", "huggingface_hub"):
        try:
            modules[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            modules[name] = False
    return {"platform": sys.platform, "machine": platform.machine(), "modules": modules}


def delegate(python, request, timeout=600):
    try:
        result = subprocess.run([python, "-m", "local_voice_studio.runtime_worker"],
                                input=json.dumps(request), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StudioError("Configured model runtime could not complete this video; inspect its setup or timeout") from exc
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise StudioError("Model runtime returned no valid result; inspect its environment and model cache") from exc
    if result.returncode:
        if payload.get("status") == "needs_retime" and isinstance(payload.get("retime_plan"), dict):
            raise TimingOverflow(payload["retime_plan"])
        raise StudioError(str(payload.get("error", "Configured model runtime failed")))
    if request.get("dry_run"):
        if payload.get("status") != "planned":
            raise StudioError("Model runtime did not return a valid plan")
        return payload
    if payload.get("status") != "succeeded":
        raise StudioError("Model runtime did not produce a successful video")
    folder = Path(request["output_dir"]).resolve()
    for name in ("video", "narration", "receipt"):
        item = payload.get("outputs", {}).get(name, {})
        path = Path(item.get("path", "")).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or sha256(path) != item.get("sha256"):
            raise StudioError("Model runtime returned an invalid output artifact")
    if payload.get("summary", {}).get("audio_present") is not True or payload.get("summary", {}).get("voice_generated") is not True:
        raise StudioError("Model runtime did not verify output audio")
    return payload


def main():
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(status()))
        return 0
    try:
        request = json.load(sys.stdin)
        allowed = {"video", "config", "output_dir", "narration", "subtitles", "reference_audio", "reference_text", "dry_run"}
        if not isinstance(request, dict) or set(request) - allowed:
            raise StudioError("Invalid isolated-runtime request")
        from .product import produce_video
        result = produce_video(**request, _worker=True)
        print(json.dumps(result))
        return 0
    except (StudioError, OSError, TypeError, ValueError) as exc:
        value = {"status": "needs_retime" if isinstance(exc, TimingOverflow) else "blocked", "error": str(exc)}
        if isinstance(exc, TimingOverflow):
            value["retime_plan"] = exc.details
        print(json.dumps(value))
        return 3 if isinstance(exc, TimingOverflow) else 2


if __name__ == "__main__":
    raise SystemExit(main())

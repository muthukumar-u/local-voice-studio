"""Bind every inference path to manifest-pinned, hash-verified model assets."""
import json
from pathlib import Path
import re
import subprocess

from .core import Config, StudioError, sha256


def ensure_pinned_assets(config: Config, allow_downloads=False, manifest_path=None) -> dict:
    manifest_path = Path(manifest_path) if manifest_path else Path(__file__).resolve().parents[2]/"model-sources.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise StudioError(f"Cannot read pinned model manifest {manifest_path}: {exc}") from exc
    commit = manifest.get("commit", "")
    if manifest.get("repository") != config.model or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise StudioError("Pinned model manifest repository/commit is invalid")
    entries = {entry.get("filename"): entry for entry in manifest.get("files", [])}
    cache = config.cache/"huggingface/hub"/("models--"+config.model.replace("/","--"))
    snapshot = cache/"snapshots"/commit
    verified = {}
    for filename in ("config.json", "kokoro-v1_0.pth", f"voices/{config.voice}.pt"):
        entry = entries.get(filename)
        if not entry or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise StudioError(f"No verified identity for {filename} in model-sources.json; add a verified pinned asset before using this voice")
        expected_url = f"https://huggingface.co/{config.model}/resolve/{commit}/{filename}"
        if entry.get("url") != expected_url:
            raise StudioError(f"Manifest URL is not bound to its exact pinned commit: {filename}")
        destination = snapshot/filename
        if not destination.is_file():
            if not allow_downloads:
                raise StudioError(f"Pinned model asset is missing: {filename} at {commit}; run prepare while connected")
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name+".partial")
            try:
                subprocess.run(["curl", "-f", "-sS", "--max-time", "600", "-L", expected_url, "-o", str(partial)], check=True)
                if sha256(partial) != entry["sha256"]:
                    partial.unlink(missing_ok=True)
                    raise StudioError(f"Downloaded pinned model asset SHA-256 mismatch: {filename}")
                partial.replace(destination)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise StudioError(f"Pinned public-asset download failed for {filename}: {exc}") from exc
        if sha256(destination) != entry["sha256"]:
            raise StudioError(f"Cached pinned model asset SHA-256 mismatch: {filename}; restore the verified asset before inference")
        verified[filename] = entry["sha256"]
    # KPipeline's public API resolves main; bind it locally, then force offline loading.
    ref = cache/"refs/main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(commit)  # No newline: huggingface_hub uses exact ref contents.
    return {"repository":config.model,"revision":commit,"assets":verified}

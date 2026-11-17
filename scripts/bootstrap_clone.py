"""Create an isolated optional cloning runtime; never change the Kokoro runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-model", action="store_true", help="Explicitly populate the selected public Base-model cache")
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("This cloning profile requires Apple Silicon macOS")
    runtime = ROOT / ".clone-venv"
    marker_path = ROOT / ".tools/clone-environment.json"
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
    if runtime.exists():
        if runtime.is_symlink() or not marker or marker.get("owner") != "local-voice-studio-clone":
            raise ValueError("Existing .clone-venv is not a marked project-owned runtime; it was preserved")
        if marker.get("project_root") != str(ROOT) or os.environ.get("VOICE_RECREATE_CLONE_ENV") == "1":
            if os.environ.get("VOICE_RECREATE_CLONE_ENV") != "1":
                raise ValueError("Cloning runtime moved; use VOICE_RECREATE_CLONE_ENV=1 sh setup-clone.sh to recreate only its marked environment")
            shutil.rmtree(runtime)
    base = ROOT / ".venv/bin/python"
    if not runtime.exists():
        selected = str(base) if base.exists() else sys.executable
        subprocess.run([selected, "-c", "import sys; assert (3,11)<=sys.version_info[:2]<(3,13), 'Use Python 3.11/3.12 or run setup.sh first'"], check=True)
        subprocess.run([selected, "-m", "venv", str(runtime)], check=True)
    python = runtime / "bin/python"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"owner": "local-voice-studio-clone", "project_root": str(ROOT), "runtime": str(runtime)}, indent=2)+"\n")
    env = os.environ.copy()
    env["PIP_CACHE_DIR"] = str(ROOT / ".cache/pip")
    wheels = []
    for item in json.loads((ROOT / "clone-platform-sources.json").read_text())["artifacts"]:
        filename = item["filename"]
        if Path(filename).name != filename or not item["url"].startswith("https://"):
            raise ValueError("Invalid clone platform artifact")
        target = ROOT / ".cache/clone-wheels" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            partial = target.with_suffix(".partial")
            subprocess.run(["curl", "-f", "-sS", "--max-time", "180", "-L", item["url"], "-o", str(partial)], check=True)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != item["sha256"]:
                partial.unlink()
                raise ValueError("Clone platform artifact checksum mismatch")
            partial.replace(target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Cached clone platform artifact checksum mismatch")
        wheels.append(str(target))
    pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
    subprocess.run([*pip, "install", "--no-index", "--no-deps", *wheels], check=True, env=env)
    hardware = subprocess.run([str(python), "-c", "import mlx.core as mx; mx.set_default_device(mx.gpu); x=mx.array([1.0]); mx.eval(x+x)"], capture_output=True, text=True)
    if hardware.returncode:
        detail = "Metal GPU is unavailable in this runtime; run cloning setup in a Mac session with GPU access."
        (ROOT / ".tools/clone-readiness.json").write_text(json.dumps({"status":"blocked","reason":detail,"model_downloaded":False}, indent=2)+"\n")
        raise ValueError(detail)
    subprocess.run([*pip, "install", "setuptools>=68", "wheel"], check=True, env=env)
    subprocess.run([*pip, "install", "--no-build-isolation", "-e", ".[clone]"], cwd=ROOT, check=True, env=env)
    if args.download_model:
        from importlib import util
        if util.find_spec("tomllib") is None:
            raise ValueError("Run this bootstrap with Python 3.11 or newer")
        import tomllib
        profile = ROOT / "config/mlx-qwen-base.toml"
        settings = tomllib.loads(profile.read_text())
        cache = (profile.parent / settings["paths"]["cache"]).resolve() / "huggingface/hub"
        subprocess.run([str(python), "-c", "from huggingface_hub import snapshot_download; import sys; print(snapshot_download(sys.argv[1],cache_dir=sys.argv[2]))", settings["voice"]["model"], str(cache)], env=env, check=True)
    print("Isolated cloning runtime configured. Model/audio/listening readiness requires a real reference-conditioned render.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

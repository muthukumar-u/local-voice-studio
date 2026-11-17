"""Create/recreate project-owned tools; never modify a global Python or shell config."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
OWNER = "local-voice-studio"


def run(args, env):
    subprocess.run([str(a) for a in args], cwd=ROOT, env=env, check=True)


def managed_environment():
    env = os.environ.copy()
    env.update({
        "UV_CACHE_DIR": str(ROOT/".cache/uv"),
        "UV_PYTHON_INSTALL_DIR": str(ROOT/".tools/python"),
        "UV_PYTHON_BIN_DIR": str(ROOT/".tools/bin"),
        "PIP_CACHE_DIR": str(ROOT/".cache/pip"),
        "npm_config_cache": str(ROOT/".cache/npm"),
    })
    return env


def check_relocation(marker: dict | None, root: Path, venv_exists: bool, recreate: bool):
    if not venv_exists:
        return False
    if not marker or marker.get("owner") != OWNER:
        raise RuntimeError("Existing .venv is not marked as owned by Local Voice Studio; choose an empty project folder instead of overwriting it")
    moved = marker.get("project_root") != str(root.resolve())
    if moved and not recreate:
        raise RuntimeError("Project moved: installed .venv shebangs are not relocatable. Run VOICE_RECREATE_ENV=1 sh setup.sh to recreate this marked generated environment")
    return moved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Inspect the installed project toolchain without downloads or changes")
    parser.add_argument("--offline", action="store_true", help="Install from previously verified project wheelhouse/interpreter; make no network requests")
    args = parser.parse_args(argv)
    env = managed_environment()
    marker_path = ROOT/".tools/environment.json"
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
    venv = ROOT/".venv"
    moved = check_relocation(marker, ROOT, venv.exists(), os.environ.get("VOICE_RECREATE_ENV") == "1")
    if args.check:
        if not venv.is_dir() or moved:
            raise RuntimeError("A current project-owned .venv has not been created")
        run([venv/"bin/python", "-c", "import sys; assert (3,11)<=sys.version_info[:2]<(3,13); print(sys.version.split()[0])"], env)
        print("Project-owned environment location is current; use voice-studio doctor for model/media readiness")
        return 0
    (ROOT/".tools").mkdir(exist_ok=True)
    (ROOT/".cache").mkdir(exist_ok=True)
    python = os.environ.get("VOICE_PYTHON")
    existing = ROOT/".tools/python/standalone-3.12/python/bin/python3.12"
    uv = ROOT/".tools/python-tools/bin/uv"
    archive = ROOT/".tools/python312.tar.gz"
    if not python and not existing.is_file() and archive.is_file():
        sources = json.loads((ROOT/"toolchain-sources.json").read_text())
        expected = sources["python"]["sha256"]
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Managed Python archive SHA-256 mismatch")
        destination = ROOT/".tools/python/standalone-3.12"
        destination.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive) as stream:
            stream.extractall(destination,filter="data")
    if not python and existing.is_file():
        python = str(existing)
    if not python:
        if args.offline:
            raise RuntimeError("Offline setup needs the verified Python archive first; run scripts/fetch_artifacts.py --plan")
        if not uv.is_file():
            run([sys.executable, "-m", "pip", "install", "--target", ROOT/".tools/python-tools", "uv==0.13.8"], env)
        run([uv, "python", "install", "3.12", "--no-bin", "--no-progress"], env)
        result = subprocess.run([str(uv), "python", "find", "3.12", "--managed-python"], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        python = result.stdout.strip()
    run([python, "-c", "import sys; assert (3,11)<=sys.version_info[:2]<(3,13), 'Use Python3.11 or3.12'"], env)
    if moved:
        if venv.is_symlink():
            raise RuntimeError("Refusing to recreate a symlinked .venv")
        shutil.rmtree(venv)  # Only a marked, project-owned generated environment.
    if not venv.exists():
        run([python, "-m", "venv", venv], env)
    marker_path.write_text(json.dumps({"owner":OWNER,"project_root":str(ROOT),"python":str(python)},indent=2)+"\n")
    pip = [venv/"bin/python", "-m", "pip"]
    runtime_minor = subprocess.run([str(venv/"bin/python"),"-c","import sys; print(sys.version_info.minor)"],capture_output=True,text=True,check=True).stdout.strip()
    if args.offline:
        pip += ["--disable-pip-version-check"]
    install_options = ["--no-index", "--find-links", str(ROOT/".tools/wheelhouse")] if args.offline else []
    run(pip+["install", *install_options, "setuptools>=68", "wheel"], env)
    locked = ROOT/"requirements-macos-arm64-py312.lock"
    if sys.platform == "darwin" and platform.machine() == "arm64" and runtime_minor == "12" and locked.exists():
        # The supplied lock captures this macOS ARM/Python3.12 production environment.
        if args.offline:
            records = json.loads((ROOT/"dependency-sources.json").read_text())["packages"]
            offline_lock = ROOT/".tools/requirements-offline.lock"
            offline_lock.write_text("\n".join(f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}" for r in records)+"\n")
            locked = offline_lock
        run(pip+["install", *install_options, "--require-hashes", "--no-build-isolation", "-r", locked], env)
        run(pip+["install", "--no-build-isolation", "--no-deps", "-e", "."], env)
    else:
        run(pip+["install", "--no-build-isolation", "-e", ".[voice]"], env)
        run([venv/"bin/python", "-m", "spacy", "download", "en_core_web_sm"], env)
    if sys.platform == "darwin" and platform.machine() == "arm64":
        binaries = [ROOT/f".tools/media/node_modules/@{tool}-installer/darwin-arm64/{tool}" for tool in ("ffmpeg","ffprobe")]
        if args.offline and not all(binary.is_file() for binary in binaries):
            raise RuntimeError("Offline setup needs project-local FFmpeg/ffprobe already populated; use the documented npm command while connected")
        if not args.offline:
            run(["npm", "install", "--prefix", ROOT/".tools/media", "--cache", ROOT/".cache/npm", "--ignore-scripts", "@ffmpeg-installer/darwin-arm64@4.1.5", "@ffprobe-installer/darwin-arm64@5.0.1"], env)
        for tool in ("ffmpeg", "ffprobe"):
            binary = ROOT/f".tools/media/node_modules/@{tool}-installer/darwin-arm64/{tool}"
            binary.chmod(binary.stat().st_mode | 0o111)
    else:
        print("Configure [mux].ffmpeg and ffprobe for this platform; the example uses macOS ARM packages")
    print("Environment ready. Populate the project model cache with: .venv/bin/voice-studio prepare --config config/explainer.toml")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

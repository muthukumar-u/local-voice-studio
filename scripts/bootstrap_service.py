"""Install the pinned upload/API layer into this project's existing runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    python = ROOT / ".venv/bin/python"
    if not python.is_file():
        raise ValueError("Create this project's runtime first with setup.sh, or install .[service] into your own Python environment")
    marker = json.loads((ROOT / ".tools/environment.json").read_text())
    if marker.get("owner") != "local-voice-studio" or marker.get("project_root") != str(ROOT):
        raise ValueError("Runtime ownership/location mismatch; follow README environment recreation first")
    files = []
    for item in json.loads((ROOT / "service-sources.json").read_text())["artifacts"]:
        filename = item["filename"]
        if Path(filename).name != filename or not item["url"].startswith("https://") or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("Invalid pinned service artifact")
        target = ROOT / ".cache/service-wheels" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            if args.offline:
                raise ValueError(f"Missing cached service dependency: {filename}")
            partial = target.with_suffix(".partial")
            subprocess.run(["curl", "-f", "-sS", "--max-time", "120", "-L", item["url"], "-o", str(partial)], check=True)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != item["sha256"]:
                partial.unlink()
                raise ValueError(f"Service artifact hash mismatch: {filename}")
            partial.replace(target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Cached service artifact hash mismatch: {filename}")
        files.append(str(target))
    pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
    subprocess.run([*pip, "install", "--no-cache-dir", "--no-index", "--no-deps", *files], cwd=ROOT, check=True)
    subprocess.run([*pip, "install", "--no-cache-dir", "--no-deps", "--no-build-isolation", "-e", "."], cwd=ROOT, check=True)
    subprocess.run([*pip, "check"], cwd=ROOT, check=True)
    print("Upload/API runtime ready; run .venv/bin/python -m local_voice_studio serve")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

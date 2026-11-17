"""Plan or fetch pinned public artifacts into this project, checking every SHA-256."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def artifact_plan(root=ROOT, include_model=False):
    dependencies=json.loads((root/"dependency-sources.json").read_text())["packages"]
    tools=json.loads((root/"toolchain-sources.json").read_text())
    items=[]
    for item in dependencies:
        filename=item["filename"]
        if Path(filename).name!=filename:
            raise ValueError("Artifact filenames must be simple names")
        items.append({"url":item["url"],"sha256":item["sha256"],"path":str(root/".tools/wheelhouse"/filename)})
    for name,destination in [("python",root/".tools/python312.tar.gz"),("uv",root/".tools/wheelhouse"/tools["uv"]["filename"])]:
        items.append({"url":tools[name]["url"],"sha256":tools[name]["sha256"],"path":str(destination)})
    if include_model:
        model=json.loads((root/"model-sources.json").read_text())
        snapshot=root/".cache/huggingface/hub/models--hexgrad--Kokoro-82M/snapshots"/model["commit"]
        for item in model["files"]:
            filename=Path(item["filename"])
            if filename.is_absolute() or ".." in filename.parts:
                raise ValueError("Model filenames must stay within the pinned snapshot")
            items.append({"url":item["url"],"sha256":item["sha256"],"path":str(snapshot/filename)})
    for item in items:
        if not item["url"].startswith("https://") or len(item["sha256"])!=64:
            raise ValueError("Artifacts require an HTTPS source and full SHA-256")
    return items


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan",action="store_true",help="Print public URL/path/hash requests without downloads or changes")
    parser.add_argument("--include-model",action="store_true")
    args=parser.parse_args(argv)
    items=artifact_plan(include_model=args.include_model)
    if args.plan:
        print(json.dumps(items,indent=2));return 0
    for item in items:
        path=Path(item["path"])
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item["sha256"]:
                raise ValueError(f"Existing artifact hash mismatch: {path}")
            continue
        path.parent.mkdir(parents=True,exist_ok=True)
        partial=path.with_name(path.name+".partial")
        subprocess.run(["curl","-f","-sS","--max-time","600","-L",item["url"],"-o",str(partial)],check=True)
        if hashlib.sha256(partial.read_bytes()).hexdigest()!=item["sha256"]:
            partial.unlink()
            raise ValueError(f"Downloaded artifact hash mismatch: {path.name}")
        partial.replace(path)
        print(path.name,flush=True)
    if args.include_model:
        model=json.loads((ROOT/"model-sources.json").read_text())
        ref=ROOT/".cache/huggingface/hub/models--hexgrad--Kokoro-82M/refs/main"
        ref.parent.mkdir(parents=True,exist_ok=True)
        ref.write_text(model["commit"])  # HF hub expects exactly 40 chars, without newline.
    return 0


if __name__=="__main__":
    try:raise SystemExit(main())
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr);raise SystemExit(2)

# Local Voice Studio

A reusable voice/video service with configuration-selected models. Applications can call its Python function, CLI, or multipart HTTP API. The upload page uses the same API.

| Input | Output |
| --- | --- |
| Video + narration text | MP4 with generated narration |
| Video + SRT/VTT | MP4 with timed narration and burned subtitles |
| Either mode + reference recording and transcript | Reference-conditioned voice when the selected cloning profile/runtime supports it |

Narration comes from the supplied text or subtitle cues. Original video audio is replaced. The video retains its display dimensions, orientation, frame rate and duration. Whole phrases must fit; an overrun returns `needs_retime` instead of dropping words.

## Start locally

Run from this repository, using its Python 3.12 environment:

```sh
sh setup.sh
sh setup-service.sh
.venv/bin/python -m local_voice_studio serve
```

Open `http://127.0.0.1:8765` for uploads or `/docs` for the generated HTTP schema. Model preparation, pinned/offline installation, relocation and platform notes are in [local setup](docs/LOCAL-SETUP.md). A remote-provider deployment can install only `.[service]`; Kokoro dependencies are the optional `voice` extra. Keep the selected profiles outside application source when they contain private paths; credentials are read from environment variables.

## Process files

```sh
.venv/bin/python -m local_voice_studio process \
  --video input.mp4 --text "A clear explanation starts here." \
  --config config/explainer.toml --output-dir outputs/my-narration

.venv/bin/python -m local_voice_studio process \
  --video input.mp4 --subtitles captions.srt \
  --config config/explainer.toml --output-dir outputs/my-subtitles
```

`--narration script.txt` can replace literal `--text`; SRT and plain WebVTT are supported. Each output directory is a new run; existing runs are preserved. Successful runs return JSON with the final MP4, raw WAV, receipt and verified hashes. Full logs/private inputs stay inside ignored outputs.

## Call from an application

```python
from pathlib import Path
from local_voice_studio.product import produce_video

result = produce_video(
    video=Path("input.mp4"), subtitles=Path("captions.srt"),
    config=Path("config/explainer.toml"), output_dir=Path("outputs/app-job"),
)
video = result["outputs"]["video"]["path"]
```

HTTP clients upload files to `POST /v1/videos`, poll the returned status URL, and download the successful result. Example:

```sh
curl -F video=@input.mp4 -F subtitles=@captions.srt \
  -F profile=default http://127.0.0.1:8765/v1/videos
```

See [HTTP API](docs/HTTP-API.md) for the full contract and browser-origin configuration. No Tech Distiller episode folders or sidecars are required by these interfaces. The earlier hash-bound [client job protocol](docs/JOB-PROTOCOL.md) remains available for existing integrations.

## Choose a model or reference voice

| Profile | Provider |
| --- | --- |
| `config/explainer.toml` | Working local Kokoro preset voice |
| `config/openai-compatible.toml` | Configured local or hosted compatible speech endpoint |
| `config/mlx-qwen-base.toml` | Optional Qwen3-TTS Base reference voice in an isolated Apple Silicon runtime |

A model must implement its selected adapter's contract; changing an arbitrary model name cannot create compatibility. Add another adapter when a model uses a different API. Provider fields and current capabilities are documented in [providers](docs/PROVIDERS.md).

For cloning, run `sh setup-clone.sh --download-model` in a Mac session with Metal GPU access. It creates only `.clone-venv`, leaving the Kokoro runtime intact. The profile's `[provider].python` sends the complete job to that interpreter. Start the service with named profiles:

```sh
.venv/bin/python -m local_voice_studio serve \
  --profile default=config/explainer.toml \
  --profile my-voice=config/mlx-qwen-base.toml
```

Select `my-voice`, upload your reference recording and the words spoken in it, then provide video + narration or subtitles. WAV, MP3, M4A, OGG and other FFmpeg-readable recordings are normalized privately to WAV; use a clear short sample, at most 60 seconds. The same reference can instead live in the profile. Kokoro and the compatible speech adapter use configured preset voices and reject reference cloning. Setup success does not establish speaker similarity; review an actual output.

## Validation and current limits

`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` exercises input validation, timing, providers, immutable jobs, upload lifecycle and output integrity. Both generic modes have been rendered with real cached Kokoro speech, and both HTTP upload/download flows have been exercised against the actual ASGI application. The checked subtitle render contains visible burned captions.

This session cannot bind a listening socket or access a Metal GPU. The network-listener test is skipped for that specific reason; sample-based cloning remains unverified and its setup reports the GPU blocker. Current placement and support status are in [ops/STATE.md](ops/STATE.md). These limits must be resolved before calling the product fully deployed or cloning-ready.

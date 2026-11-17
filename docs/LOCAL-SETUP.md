# Local setup

Use Python 3.11 or 3.12. Run commands from the cloned repository. Model weights, uploads, generated media and runtime environments are stored in ignored directories.

## Local preset voice and upload service

On macOS ARM64, Python with pip and npm are enough to bootstrap the owned tools:

```sh
sh setup.sh
sh setup-service.sh
.venv/bin/python -m local_voice_studio prepare --config config/explainer.toml
.venv/bin/python -m local_voice_studio doctor --config config/explainer.toml --model-preflight --no-download
.venv/bin/python -m local_voice_studio serve
```

Open `http://127.0.0.1:8765`. Upload a video and either narration text or an SRT/VTT file. Subtitle cues supply the spoken words and burned captions. Processing preserves the input file; generated speech replaces the output video's audio.

`setup.sh` creates `.venv`, project-owned Python/tool caches, Kokoro/G2P dependencies and configured FFmpeg binaries. `setup-service.sh` adds the upload/API layer to that environment. `prepare` downloads and verifies the pinned model/voice assets and synthesizes a diagnostic phrase. The initial setup requires internet access; subsequent processing uses cached local assets.

The default profile uses Kokoro-82M, `af_heart`, English, speed 0.95 and 24 kHz mono speech. Its bounded speed ceiling is 1.15; phrases that cannot fit return a retiming error. Choose another supported provider/profile for different requirements. Configuration paths resolve relative to the profile file. See [providers](PROVIDERS.md) for exact interfaces and capabilities.

Pinned identities and public artifact provenance live in [model-sources.json](../model-sources.json), [dependency-sources.json](../dependency-sources.json), [service-sources.json](../service-sources.json) and [toolchain-sources.json](../toolchain-sources.json). Kokoro downloads verify the exact model commit and asset hashes before cache-only inference.

## Other systems and HTTP providers

For a compatible speech endpoint, create a Python 3.11/3.12 environment and install only the service extra:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[service]'
```

Install FFmpeg/ffprobe for your operating system, including the `subtitles` filter (libass), H.264 encoder (`libx264`) and AAC encoder. Set `[mux].ffmpeg` and `[mux].ffprobe` to executable names or paths in the profile. Configure the speech endpoint, model and voice in `config/openai-compatible.toml`; credentials are read from the named environment variable. Start it with:

```sh
.venv/bin/python -m local_voice_studio serve --profile default=config/openai-compatible.toml
```

For local Kokoro on another supported platform, install `.[voice,service]` and its English G2P package with `.venv/bin/python -m spacy download en_core_web_sm`, configure FFmpeg/ffprobe, and run `prepare` with that profile. The supplied full environment lock and binary manifests target macOS ARM64/Python 3.12; other systems use the declared dependency constraints and their own media binaries.

## Optional reference voice

```sh
sh setup-clone.sh --download-model
.venv/bin/python -m local_voice_studio serve \
  --profile default=config/explainer.toml \
  --profile reference=config/mlx-qwen-base.toml
```

Cloning setup creates a separate `.clone-venv`, checks Metal GPU availability, installs the optional model stack and, with `--download-model`, prepares its model cache. The profile's `[provider].python` selects this interpreter; whole jobs run there without changing the preset-voice environment.

If the Hub's optional accelerated transfer stalls, retry preparation with `HF_HUB_DISABLE_XET=1 sh setup-clone.sh --download-model` to use standard HTTP downloads. This affects preparation only; it does not authorize retrying an incomplete production job.

Select the reference profile and supply a short recording plus its exact transcript, or configure that pair in the profile. The facade normalizes supported recordings privately to WAV. Recordings over 60 seconds are rejected instead of silently trimmed. Kokoro preset voices do not support reference cloning. Listen to an actual reference-conditioned result before relying on its speaker similarity.

## File and application interfaces

Use `process --video input.mp4 --text "Narration." --config config/explainer.toml --output-dir outputs/example`, or replace `--text` with `--narration script.txt` / `--subtitles captions.srt`. Each output directory must be new or empty. Generated artifacts include MP4, raw WAV and a receipt with input/output hashes and processing details.

See [HTTP API](HTTP-API.md) for multipart uploads and [job protocol](JOB-PROTOCOL.md) for optional hash-bound requests. No external application's directory layout or identifiers are required. The advanced [sidecar interface](JOB-PROTOCOL.md#narration-sidecars) accepts local video/narration/timing paths; user inputs are treated as data.

## Offline installation and relocation

The artifact fetcher can print or populate pinned URLs and SHA-256 hashes:

```sh
python3 scripts/fetch_artifacts.py --plan --include-model
python3 scripts/fetch_artifacts.py --include-model
npm install --prefix .tools/media --cache .cache/npm --ignore-scripts \
  @ffmpeg-installer/darwin-arm64@4.1.5 @ffprobe-installer/darwin-arm64@5.0.1
sh setup.sh --offline
sh setup-service.sh --offline
```

Populate the verified Python archive, wheelhouse, media binaries and model assets before offline installation. The fetcher writes the pinned model ref without a newline, as required by the cache loader. Service artifacts have their own verified manifest: run `sh setup-service.sh` once while connected to populate `.cache/service-wheels` before an offline replay. The artifact fetcher does not populate that service cache. Cached-file hash mismatches stop setup. The documented offline installation targets the supplied macOS ARM64/Python 3.12 lock; optional cloning setup needs a connected compatible machine.

Virtual environments are location-specific. After moving a complete installed checkout, recreate only the marked owned environments:

```sh
VOICE_RECREATE_ENV=1 sh setup.sh
sh setup-service.sh
VOICE_RECREATE_CLONE_ENV=1 sh setup-clone.sh
```

Fresh clones have no installed environments and use normal setup. Unmarked environments are preserved rather than overwritten. Environments created manually with `python -m venv` use pip directly; the setup helpers intentionally refuse them without their own ownership marker. Recreate the optional cloning environment only if it was previously installed and moved. All generated runtime/cache directories stay outside Git; setup does not change global Python, shell configuration or other applications.

## Audio finishing and verification

The default delivery target is -16 LUFS with -1.5 dBFS true peak. These are configurable playback choices. The raw timed WAV is retained before delivery finishing; receipts record measured results. Cue fitting preserves complete words and does not establish word-level forced alignment. Check actual playback, pronunciation, captions and pacing.

Code/weight licensing and dependencies retain their respective upstream terms. Model licensing does not establish rights in arbitrary uploaded media or reference recordings. Source references: [Kokoro](https://github.com/hexgrad/kokoro), [model card](https://huggingface.co/hexgrad/Kokoro-82M), [MLX Audio](https://github.com/Blaizzy/mlx-audio) and [FFmpeg loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).

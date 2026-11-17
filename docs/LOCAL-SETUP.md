# Local Voice Studio

A separate, reusable local project for adding timed narration to agent-generated videos. Give the CLI the video path; it discovers the accompanying `.voice.json` sidecar and reads the narration and timings exported by the content agent. It does not infer a trustworthy script from silent frames.

For another project's agent, use the [compact job protocol](JOB-PROTOCOL.md). This repository owns setup, model/cache, locks, jobs/logs and full receipts. Client repositories need only a thin bridge, compact requests/results and delivered artifact references. Agent routing: [AGENTS.md](../AGENTS.md), [local voice skill](../skills/voice-production/SKILL.md), [support handoff](../ops/STATE.md), [change history](../CHANGELOG.md).

The default is **Kokoro-82M, `af_heart`, American English, speed 0.95**, with a bounded speed ceiling of 1.15. Actual synthesis has been verified; the preset still needs a human listening test. Configuration is independent of the YouTube channel and can be reused by any project.

## Current delivery state

The separate temporary project now has its own CPython 3.12.16, `.venv`, Kokoro 0.9.4/Misaki 0.9.4, English spaCy model, packaged espeak-ng library, and FFmpeg/ffprobe binaries. Cache-only preflight generated **3.9 seconds of nonzero audio** on 2026-11-17. A real 32-cue audit exposed overruns in the original edit, so the content agent retimed it for natural speech. The render completed at **122 seconds, 1080×1920, 30 fps**, with every cue synthesized at **0.95 speed on its first attempt**. Delivery loudness was then finished to **-16.47 LUFS integrated**, with **-1.46 dBFS true peak** and **3.8 LU loudness range**; the raw speech was -25.64 LUFS. This processing copied the existing H.264 video stream and retained subtitles/watermark. The final MP4 and full provenance are in `outputs/ES-001-natural-levelled/`; the original raw WAV and synthesis receipt remain in `outputs/ES-001-natural-voiced/`. Human listening review remains pending.

The validated dependency environment has 93 pinned distributions with artifact hashes in [requirements-macos-arm64-py312.lock](../requirements-macos-arm64-py312.lock) and public source/license metadata in [dependency-sources.json](../dependency-sources.json). The pinned model commit and verified asset identities are in [model-sources.json](../model-sources.json). Interpreter/uv and npm binary provenance are in [toolchain-sources.json](../toolchain-sources.json).

This folder was staged under `/private/tmp` because the session cannot write a sibling repository in the intended personal-project directory. Copy the project to your durable chosen location before using it; temporary storage is not a backup. Its Git repository is local only; no remote, commit, or push is created by this package.

## One-time setup

The voice runtime uses **Python 3.11 or 3.12**. The published Kokoro 0.9.4 package permits Python 3.10–3.12; this wrapper needs Python 3.11+ for stdlib TOML support. Python 3.13–3.15 cannot install that published Kokoro release, but an existing newer Python can bootstrap this project's separate Python 3.12. Do not replace the system or other projects' Python.

On macOS ARM64, setup uses an existing Python with pip plus npm to create all runtime tools inside this project; no global package or Homebrew installation is required. Other platforms need compatible FFmpeg/ffprobe paths in the config. espeak-ng comes from the pinned project-local `espeakng-loader` Python package.

From the copied project directory:

```sh
sh setup.sh
# If needed, choose an existing compatible interpreter explicitly:
# VOICE_PYTHON=/path/to/python3.12 sh setup.sh
# Or choose the existing Python used only to run the bootstrap:
# VOICE_BOOTSTRAP_PYTHON=/path/to/python3.15 sh setup.sh

.venv/bin/voice-studio prepare --config config/explainer.toml
.venv/bin/voice-studio doctor --config config/explainer.toml --model-preflight --no-download
```

`setup.sh` runs maintained [scripts/bootstrap.py](../scripts/bootstrap.py). It installs uv into `.tools/python-tools` when needed, a managed Python 3.12 under `.tools/python`, this project's `.venv`, voice/G2P dependencies, and the exact npm media binary packages at the paths used by the example config. All pip/uv/npm caches stay under `.cache`. The macOS ARM/Python3.12 path uses the hashed environment lock; other supported Python/platform combinations use the constrained voice extra. The first installation and `prepare` require internet access. `prepare` populates the project model cache and checks nonzero diagnostic audio. Dependency detection alone does not establish that the weights are cached or speech works.

The runtime was integration-tested with the locked environment, including spaCy-curated-transformers 0.3.1, which is compatible with spaCy 3.8's Thinc line. Model assets remain outside Git. Existing unmarked `.venv` folders are refused instead of overwritten.

The installed environment is **not blindly relocatable**: virtual-environment shebangs and interpreter links may contain absolute paths. Source/configuration bundles exclude `.tools`, `.venv`, `.cache` and `outputs`. After copying source to a durable location, run setup there. If moving the complete installed project, setup detects its ownership/location marker and requires `VOICE_RECREATE_ENV=1 sh setup.sh` to recreate only the marked generated `.venv`; copied model and artifact caches can be reused. It does not rewrite your shell configuration.

For restricted or offline installation, [scripts/fetch_artifacts.py](../scripts/fetch_artifacts.py) can emit every pinned public URL/path/hash as a plan for discrete downloads, or download and verify those artifacts itself on a connected host:

```sh
python3 scripts/fetch_artifacts.py --plan --include-model
# On a connected host:
python3 scripts/fetch_artifacts.py --include-model
# Populate the macOS ARM media packages while connected:
npm install --prefix .tools/media --cache .cache/npm --ignore-scripts \
  @ffmpeg-installer/darwin-arm64@4.1.5 @ffprobe-installer/darwin-arm64@5.0.1
# Then install from the verified local Python archive and wheelhouse:
sh setup.sh --offline
```

This session's direct pip/uv network lookups failed DNS even when discrete curl downloads worked. Installation therefore used verified public artifacts and the offline path; it did not tunnel or proxy traffic. The model cache's `refs/main` must contain the exact commit without a newline; the maintained fetcher writes it correctly after asset verification.

## Use with any project's exported video

```sh
.venv/bin/voice-studio plan \
  --video /path/to/episode/short.mov \
  --config config/explainer.toml

.venv/bin/voice-studio render \
  --video /path/to/episode/short.mov \
  --config config/explainer.toml
```

`plan` and `render --dry-run` require only Python stdlib and never import Kokoro or download assets. They verify video identity by hash, narration coverage and timing. Planning does not probe the media container; a real render uses ffprobe before muxing and verifies the resulting video. `doctor` exits nonzero when prerequisites are missing. Plain `doctor` reports `prerequisites_ready` separately, leaves model status `not_checked`, and does not claim the voice engine is ready. `--model-preflight --no-download` actually attempts cache-only synthesis when dependencies are ready and sets `ready=true` only after nonzero diagnostic audio is produced.

The render uses cached assets by default. `prepare` and `render --allow-downloads` may fetch only the exact commit/assets in `model-sources.json`, verify SHA-256, bind the local ref, and then construct the model in forced cache-only mode. They cannot silently follow a newer upstream main. The configured cache path is respected. A different voice needs a verified identity in the manifest first. Missing spaCy assets are reported with an installation command; they are not silently installed during a render.

Outputs are under `outputs/<video-stem>-<hash-prefix>/` by default: `narration.wav`, per-cue WAVs, `receipt.json`, and `<video-stem>_voiced.mp4`. `--output-dir PATH` chooses a different empty output directory. Existing nonempty directories are refused instead of overwritten. Original video and sidecar inputs are never modified. The final video replaces the source audio track; it does not automatically mix music or preserve other source audio.

For narration without muxing:

```sh
.venv/bin/voice-studio render --video /path/to/short.mov \
  --config config/explainer.toml --audio-only
```

This still needs the voice model. It does not need FFmpeg, and its receipt states that container metadata was not probed. The WAV is mono 24 kHz, padded to the declared video duration. FFmpeg's default final export is H.264/AAC MP4 with the original dimensions and frame rate; `video_mode="copy_mov"` preserves the source video stream in a MOV instead.

## Delivery loudness

The example `[mux]` config enables FFmpeg `loudnorm` with an editorial target of **-16 LUFS**, **-1.5 dBFS true peak**, and **11 LU loudness range**. This is a phone-playback production choice, not a YouTube requirement. AAC encoding can shift measured peaks slightly, so the receipt records both targets and actual measured delivery values. Conservative accepted config ranges are -24 to -9 LUFS, -9 to -0.1 dBFS true peak, and 1 to 20 LU range. Omit `loudness_lufs` to disable this processing.

Loudness finishing applies to the delivery video's audio during muxing. `narration.wav` remains the raw timed narration before this finishing stage. It does not change narration words, speaking rate or cue timing. To finish an existing completed H.264 video without re-synthesizing speech or re-encoding its video stream:

```sh
.venv/bin/voice-studio finish \
  --receipt outputs/ES-001-natural-voiced/receipt.json \
  --config config/explainer.toml \
  --output-dir outputs/ES-001-natural-levelled
```

The new receipt links the unchanged raw WAV and source receipt by hash, records the current config hash and measurements, and verifies encoded video-stream identity. Source artifacts are retained. Choose a fresh output directory for each finishing pass.

## Timing and quality behavior

Default `fit_mode="cue"` synthesizes each subtitle phrase into its existing SRT time window. It retries complete text at a modestly faster synthesis speed when needed, up to the configured ceiling. It never truncates words, speeds up the video, or silently changes the script. If a phrase still overruns, rendering fails with a `retime-plan.json`; the content agent must extend the affected video/caption timing or edit the narration before retrying.

Cue mode keeps phrase boundaries scheduled but is **not word-level forced alignment**. Short utterances can sound less natural with Kokoro; audition pacing, acronym pronunciation, word coverage, and transitions. `fit_mode="scene"` generates longer scene passages for more continuous phrasing, but existing subtitle timings then require rechecking. Silent gaps remain silent. Waveforms are checked for finite/nonzero samples and normalized downward when needed to avoid PCM clipping; that does not establish perceptual quality.

The optional `[pronunciation]` table performs literal, case-sensitive whole-word substitutions for synthesis, records the synthesis text in the receipt, and leaves the original subtitles unchanged. Keep it empty until listening exposes a specific pronunciation issue.

## Export contract for content agents

For a video `short.mov`, export `short.voice.json` beside it:

```json
{
  "schema_version": 1,
  "video": {
    "path": "short.mov",
    "sha256": "<actual full SHA-256 of this exact video>",
    "duration": 111.5,
    "width": 1080,
    "height": 1920,
    "fps": 30
  },
  "narration": {
    "segments_path": "voice-segments.json",
    "timeline_path": "timeline.json",
    "captions_srt_path": "captions.srt"
  },
  "target": {
    "platform": "youtube",
    "format": "shorts",
    "primary_device": "phone",
    "max_duration_seconds": 180
  }
}
```

Paths are local and relative to the sidecar, or absolute. They may point into another project's folders; this reusable tool does not require channel-specific paths. Sidecars are data, not shell commands. A changed video hash requires regenerated sidecar metadata.

`voice-segments.json` has a `segments` array with `{ "id": "S01", "start": 0, "duration": 12.5, "text": "Complete narration." }`. `end` may replace `duration`. Optional root `duration` must match the video; `schema_version` is 1 when present. Segments must be ordered, nonoverlapping and inside the video. A supplied timeline uses `{ "scenes": [{ "duration": 12.5, "narration": "Complete narration." }] }` and must match the scene segments. Plain-text SRT uses absolute video times; every narration word must appear in the same order and in its matching scene.

## Checks and sources

Run dependency-free behavior tests from the project directory:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m local_voice_studio doctor --config config/explainer.toml
```

The 39 behavior tests verify placement, bounded retries, clipping, ownership/relocation checks, artifact hash/path failures, offline reuse, manifest-pinned model loading, loudness processing, and hash-bound/idempotent client jobs. Mock production fixtures are separate from the actual successful cache-only model preflight, real 32-cue synthesis, and measured FFmpeg delivery finishing. Auditory quality still requires listening to the produced narration.

Primary sources checked 2026-11-17:

- [Kokoro official repository](https://github.com/hexgrad/kokoro) and [KPipeline source](https://github.com/hexgrad/kokoro/blob/main/kokoro/pipeline.py): language, voice, speed and 24 kHz generator interface.
- [Published Kokoro 0.9.4 metadata](https://pypi.org/project/kokoro/0.9.4/): release and Python compatibility. GitHub main's Python range differs; this project follows the published release it installs.
- [Kokoro-82M model card](https://huggingface.co/hexgrad/Kokoro-82M) and [official voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md): model/voice origin and short-utterance caveat.
- [Misaki English G2P source](https://github.com/hexgrad/misaki/blob/main/misaki/en.py): required spaCy model and fallback behavior.
- [FFmpeg loudnorm documentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm): loudness processing; actual installed FFmpeg 4.4 filter options were checked locally.
- [SoundFile](https://pypi.org/project/soundfile/), [NumPy 1.26.4](https://pypi.org/project/numpy/1.26.4/), and [Transformers 4.51.3](https://pypi.org/project/transformers/4.51.3/): initial compatibility references. Exact successfully installed versions and artifact hashes are recorded in the environment lock and dependency-source manifest.

Kokoro's code and weights are published under Apache-2.0. Dependencies, espeak-ng, FFmpeg, and any additional assets have their own licenses; model licensing does not replace those terms or establish rights in arbitrary input content. No third-party code or weights are vendored in this repository.

# Reusable local voice production

Updated: 2026-11-17. The channel exports video plus narration/timings; a separately managed Local Voice Studio project owns the model, config, isolated runtime/cache, synthesis, and audio/video finishing.

## Installed model and actual result

**Kokoro-82M / af_heart**, American English, CPU, speed **0.95**. This is a working initial preset pending the creator's listening preference; no voice training is required. The official [model card](https://huggingface.co/hexgrad/Kokoro-82M) and [library](https://github.com/hexgrad/kokoro) document the model and Apache-licensed weights. The tested library is Kokoro 0.9.4 in a project-local Python 3.12 environment, with pinned dependency/artifact hashes and model commit f3ff3571791e39611d31c381e3a41a3af07b4987.

Actual cache-only synthesis, all 32 narration cues, video muxing, and audio finishing succeeded. ES-001 is now **122 seconds, 1080×1920, 30 fps**, H264/AAC, with burned captions and the persistent watermark. Its caption timings were rebuilt from measured speech instead of forcing words into the earlier 111.5-second estimate. Every cue fit at 0.95 speed on its first final attempt.

The final audio measured **−16.47 LUFS / −1.46 dBFS true peak**. Configured targets −16 LUFS, −1.5 dBFS and 11 LU range are editorial choices, not YouTube requirements. Raw model WAV stays unchanged; the delivered AAC is levelled. Human voice preference and continuous playback review remain pending.

## Separate project and config

- Working Git project: `/private/tmp/local-voice-studio`. It has its own Python 3.12, `.venv`, `.tools`, model cache, source/config, and outputs. No global Python or shell/client settings were changed; no commit, remote or push was created.
- Voice config: `/private/tmp/local-voice-studio/config/explainer.toml`. Model/voice/language/speed, pronunciation, cue fitting, cache/output locations, codec, and audio-level targets live there.
- Desired durable sibling location: `/Users/muthu/Programs/personal/local-voice-studio`. This session cannot write that directory; the independent project was built in an allowed temporary root.
- Portable source: [local-voice-studio.tar.gz](../media/voice-tools/local-voice-studio.tar.gz). It includes source/config/tests, maintained bootstrap/fetch scripts, hashed dependency locks, and model/toolchain source manifests. It excludes Git metadata, installed environments/tools, caches/model binaries, and outputs.

Temporary storage is not a backup. Copy/extract the source into an empty durable project directory and run setup there. If moving the complete installed project, virtual-environment shebangs and interpreter links may contain old absolute paths: recreate the marked project-owned environment with `VOICE_RECREATE_ENV=1 sh setup.sh`. Copied artifact/model caches can be reused. Unmarked environments are refused. Update channel.json production_tools.voice project/config references after relocation.

## Use it with a generated video

Run from the separate project:

```bash
cd /private/tmp/local-voice-studio
.venv/bin/voice-studio render \
  --video /Users/muthu/Programs/personal/tech-distiller/media/ES-001/ES-001-shorts-subtitled.mp4 \
  --config config/explainer.toml
```

Pass only the video path. The tool automatically finds its `.voice.json` companion, verifies exact video identity, and follows references to the agent-generated narration/captions/timing. Those companion assets must travel with or remain reachable from the video; the tool does not guess an explanation from silent pixels. Input can come from any project using the same export contract.

The subtitled input retains captions and the watermark; the clean variant supports separately styled captions. Output goes into the voice project's outputs/ (or an explicitly chosen empty directory): raw narration/clips, voiced MP4, and receipts. Original inputs remain untouched. `render --audio-only` returns raw narration without muxing. `finish --receipt <prior-receipt> --config config/explainer.toml --output-dir <empty-folder>` can redo audio levels from existing speech with the video stream copied unchanged.

Use `plan` or `render --dry-run` for a dependency-free contract/timing check. For actual cached-model readiness:

```bash
.venv/bin/voice-studio doctor --config config/explainer.toml --model-preflight --no-download
```

Ordinary doctor deliberately distinguishes prerequisite presence from tested model synthesis. Real cache-only preflight must pass before calling the model ready.

## Install or recreate in a durable location

From the copied source directory on this Mac:

```bash
sh setup.sh
.venv/bin/voice-studio prepare --config config/explainer.toml
.venv/bin/voice-studio doctor --config config/explainer.toml --model-preflight --no-download
```

Maintained setup uses an existing Python with pip and npm to bootstrap project-owned uv/Python 3.12, dependencies, English assets, bundled espeak-ng, and the exact media binaries used by the config. First installation requires downloads. The source includes a verified artifact fetcher and offline mode; an actual `sh setup.sh --offline` replay passed with the staged caches. This managed session used discrete approved curl downloads when direct pip/uv DNS failed. Other hardware needs compatible runtime/media binaries as explained in the standalone README.

Config/cache/output paths are relative to the voice project, not the channel. The model manifest pins the actual revision and asset hashes; even download-enabled loading verifies the pinned assets before offline inference. Editing a voice/language choice requires its matching supported, pinned voice asset to be available; the included initial asset is af_heart. Do not claim an untested preset was auditioned. Package/model dependencies retain their own licenses; none of their binaries are included in the source bundle.

## Production and tracking

Measure speech before locking high-resolution timing. Default cue mode schedules full subtitle phrases and can retry synthesis within a bounded speed range. Unresolvable overrun produces a retiming plan; words are never truncated. Cue scheduling is not word-level forced alignment. ES-001 was extended to 2:02 to retain the natural 0.95 pace.

The content agent generates companions with `scripts/prepare_video.py --video <actual-export>`; these contain video hashes, relative narration paths and delivery targets. Regenerate after any video-byte change. Each episode manifest owns its current video/audio/setup/receipt references. [ES-001 setup evidence](../content/ES-001-what-happens-when-you-send-a-prompt/production/voice-setup.json), [timing audit](../content/ES-001-what-happens-when-you-send-a-prompt/production/voice-timing-audit.json), [synthesis receipt](../content/ES-001-what-happens-when-you-send-a-prompt/production/voice-receipt.json), and [delivery receipt](../content/ES-001-what-happens-when-you-send-a-prompt/production/voice-delivery-receipt.json) record actual results. Agent review does not authorize publication.

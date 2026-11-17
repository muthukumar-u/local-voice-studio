# Client job protocol v1

Invoke from any working directory using an absolute interpreter and request path:

```sh
<voice-repo>/.venv/bin/python -m local_voice_studio job --request /absolute/request.json
```

`--dry-run` validates and plans without creating a job directory or loading the speech model. It returns `status: "succeeded"`, `dry_run: true`, empty outputs and false audio/voice flags; this is successful planning, not generated content.

## Request

Required fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `job_id` | Safe 1–128-character identifier, no path separators |
| `client`, `item_id` | Nonempty caller and media-item identifiers; `episode_id` remains an accepted compatibility alias for `item_id` |
| `action` | `render` |
| `video`, `sidecar` | Each `{path: absolute local path, sha256: full lowercase SHA-256}` |
| `profile` | `{path: local profile path, sha256: full lowercase SHA-256}`; relative paths resolve within the voice repo, and absolute profiles must stay inside it |
| `inputs_sha256` | Exact map from absolute resolved paths to hashes for every sidecar-referenced segments/SRT/timeline asset; sidecar itself has its separate descriptor |
| `expectations` | Exactly `{width, height, fps, duration}`; geometry/fps/duration must match the sidecar |

Optional `options` contains boolean `audio_only` and `allow_downloads`, both false by default. A normal job returns video plus raw narration; delivery muxing applies its profile's configured loudness finishing. `attempt_nonce` is an optional nonempty string for an explicitly intended new attempt.

See [narration sidecars](#narration-sidecars) for the narration/timing export schema. Ordinary `process` and upload clients do not need this advanced format.

Compute the request fingerprint as UTF-8 SHA-256 of:

```python
json.dumps({k: v for k, v in request.items() if k != "job_id"},
           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

A caller can use `media-` plus a fingerprint prefix as a safe identifier. Different request data must not reuse an existing job ID.

## Storage and retries

The voice repo owns `outputs/jobs/<job_id>/request.json`, request fingerprint, `run.log`, `result.json` and `artifacts/` with the full production receipt/media. These paths are ignored by Git. Clients keep only the compact request/result and chosen artifact references or delivered copies.

A matching succeeded retry revalidates current inputs/profile snapshots and every output hash, waveform and media summary before returning the stored result with `reused: true`. It does not synthesize again. A conflicting, running, incomplete, blocked or failed job is never silently overwritten or automatically retried. Use an explicitly intended new `attempt_nonce` and recomputed ID for another attempt. Relocated historical output paths must be revalidated; old absolute paths are not silently accepted as the new durable location.

## Result

Stdout contains one compact JSON object:

```json
{
  "schema_version": 1,
  "job_id": "client-item-fingerprint",
  "status": "succeeded",
  "input_video_sha256": "<actual input hash>",
  "profile_sha256": "<actual profile hash>",
  "outputs": {
    "video": {"path": "<voice-repo output>", "sha256": "<actual hash>"},
    "narration": {"path": "<raw timed WAV>", "sha256": "<actual hash>"},
    "receipt": {"path": "<full voice-repo receipt>", "sha256": "<actual hash>"}
  },
  "summary": {
    "width": 360, "height": 640, "fps": 30, "duration": 8,
    "audio_present": true, "voice_generated": true
  }
}
```

Statuses and exit codes: `succeeded` → 0, `blocked` → 2, `failed` → 1, `needs_retime` → 3. Unsuccessful results include `error: {code, message}` and only artifacts actually available. Full per-clip timing, model provenance, setup and processing details remain in the voice repository.

Clients should independently verify returned hashes and expected summary before copying/promoting an output. A success result establishes technical production checks, not human listening approval, publication permission or resolution of an unrelated playback symptom.

## Narration sidecars

The `plan`, `render` and hash-bound `job` interfaces accept exported narration/timing metadata. Ordinary `process`, Python `produce_video` and HTTP uploads accept video plus text or SRT/VTT directly and do not require these files.

For `clip.mp4`, export `clip.voice.json` beside it:

```json
{
  "schema_version": 1,
  "video": {
    "path": "clip.mp4",
    "sha256": "<actual full SHA-256 of this exact video>",
    "duration": 8,
    "width": 360,
    "height": 640,
    "fps": 30
  },
  "narration": {
    "segments_path": "segments.json",
    "captions_srt_path": "captions.srt"
  }
}
```

Paths resolve relative to the sidecar, or may be absolute local paths. A changed video needs a new hash and accurate metadata. `segments.json` supplies complete scene passages:

```json
{
  "schema_version": 1,
  "duration": 8,
  "segments": [
    {"id": "S01", "start": 0, "duration": 8, "text": "Hello world. This is a local voice studio example."}
  ]
}
```

`end` can replace segment `duration`. Segments must be ordered, nonoverlapping and inside the video. Plain-text SRT must contain every narration word in order and inside the corresponding segment. The optional `timeline_path` points to JSON with `scenes`, each containing `duration` and `narration`; its passages and duration must match the segments. Optional `target.max_duration_seconds` limits total duration.

```sh
.venv/bin/python -m local_voice_studio plan --video clip.mp4 --config config/explainer.toml
.venv/bin/python -m local_voice_studio render --video clip.mp4 --config config/explainer.toml
```

`plan` and `render --dry-run` validate source identity, coverage and timing without loading a model or probing the media container. A real render checks media with ffprobe, uses cached speech assets by default and replaces the original audio. `--audio-only` produces raw narration WAV without muxing. Choose a fresh `--output-dir` to preserve prior runs.

Default cue fitting retries complete phrases up to the configured synthesis-speed ceiling. Overflow requires retiming the video/captions or editing narration; words are never truncated. Scene fitting uses longer passages and requires caption alignment review. Cue placement is not word-level forced alignment. Optional `[pronunciation]` substitutions change synthesis text only and are recorded in receipts.

The default delivery target is -16 LUFS and -1.5 dBFS true peak; omit `loudness_lufs` to disable finishing. Raw timed WAV remains unchanged. To finish an existing completed render without new speech or video re-encoding, choose a fresh output directory:

```sh
.venv/bin/python -m local_voice_studio finish \
  --receipt outputs/source-render/receipt.json \
  --config config/explainer.toml --output-dir outputs/finished-render
```

The finishing receipt links source artifacts by hash and records video-stream identity and measured loudness. Receipts and generated media stay in ignored storage.

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
| `client`, `episode_id` | Nonempty client identifiers |
| `action` | `render` |
| `video`, `sidecar` | Each `{path: absolute local path, sha256: full lowercase SHA-256}` |
| `profile` | `{path: local profile path, sha256: full lowercase SHA-256}`; relative paths resolve within the voice repo, and absolute profiles must stay inside it |
| `inputs_sha256` | Exact map from absolute resolved paths to hashes for every sidecar-referenced segments/SRT/timeline asset; sidecar itself has its separate descriptor |
| `expectations` | Exactly `{width, height, fps, duration}`; geometry/fps/duration must match the sidecar |

Optional `options` contains boolean `audio_only` and `allow_downloads`, both false by default. A normal job returns video plus raw narration; delivery muxing applies its profile's configured loudness finishing. `attempt_nonce` is an optional nonempty string for an explicitly intended new attempt.

Compute the request fingerprint as UTF-8 SHA-256 of:

```python
json.dumps({k: v for k, v in request.items() if k != "job_id"},
           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

Tech Distiller's convention is `tech-distiller-ES-001-` plus the first 20 fingerprint hex characters. Another client can choose a different safe identifier. Different request data must not reuse an existing job ID.

## Storage and retries

The voice repo owns `outputs/jobs/<job_id>/request.json`, request fingerprint, `run.log`, `result.json` and `artifacts/` with the full production receipt/media. These paths are ignored by Git. Clients keep only the compact request/result and chosen artifact references or delivered copies.

A matching succeeded retry revalidates current inputs/profile snapshots and every output hash, waveform and media summary before returning the stored result with `reused: true`. It does not synthesize again. A conflicting, running, incomplete, blocked or failed job is never silently overwritten or automatically retried. Use an explicitly intended new `attempt_nonce` and recomputed ID for another attempt. Relocated historical output paths must be revalidated; old absolute paths are not silently accepted as the new durable location.

## Result

Stdout contains one compact JSON object:

```json
{
  "schema_version": 1,
  "job_id": "client-episode-fingerprint",
  "status": "succeeded",
  "input_video_sha256": "<actual input hash>",
  "profile_sha256": "<actual profile hash>",
  "outputs": {
    "video": {"path": "<voice-repo output>", "sha256": "<actual hash>"},
    "narration": {"path": "<raw timed WAV>", "sha256": "<actual hash>"},
    "receipt": {"path": "<full voice-repo receipt>", "sha256": "<actual hash>"}
  },
  "summary": {
    "width": 1080, "height": 1920, "fps": 30, "duration": 122,
    "audio_present": true, "voice_generated": true
  }
}
```

Statuses and exit codes: `succeeded` → 0, `blocked` → 2, `failed` → 1, `needs_retime` → 3. Unsuccessful results include `error: {code, message}` and only artifacts actually available. Full per-clip timing, model provenance, setup and processing details remain in the voice repository.

Clients should independently verify returned hashes and expected summary before copying/promoting an output. A success result establishes technical production checks, not human listening approval, publication permission or resolution of an unrelated playback symptom.

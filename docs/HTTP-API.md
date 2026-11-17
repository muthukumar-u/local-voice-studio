# Local upload service

The local HTTP service accepts a video plus either complete narration text or a timed SRT/VTT file. It delegates production to the same reusable `product.produce_video` facade as the command-line product. It does not infer narration from silent video frames or require the caller to manufacture sidecars.

The parent CLI exposes the service startup command. Application integrations can create the app directly:

```python
from pathlib import Path
import uvicorn
from local_voice_studio.api import create_app

app = create_app(
    profiles={"default": Path("/absolute/voice-repo/config/explainer.toml")},
    data_dir=Path("/absolute/voice-repo/outputs/http"),
    allowed_origins=["http://localhost:3000"],  # Optional browser client origin.
)
uvicorn.run(app, host="127.0.0.1", port=8765, workers=1)
```

Use a single server process: one worker thread serializes all model execution to avoid overlapping model/environment/GPU operations. Uploads, status queries and downloads remain available while a job processes. Keep the service's data directory inside ignored runtime storage. Profiles are configured on the server; an upload cannot select an arbitrary configuration path.

Open `/` for the same-origin upload page, profile selector, optional supported voice recording, progress and download link. The UI needs no frontend build. `/health` reports service availability; it does not load or audition a voice model. `/v1/profiles` reports only configured names, engine, safe capability flags and prerequisite status. A ready prerequisite report does not claim that a recording was cloned or audio was human-reviewed.

Cross-origin browser clients can be enabled with explicit `allowed_origins`; the service then permits GET/POST and the Content-Type header for those origins. Wildcard origins, embedded credentials and origin paths are rejected. The CLI's repeatable `--allow-origin` supplies this list. Omitting it keeps the built-in same-origin UI working without enabling other browser origins. CORS is browser access policy, not authentication.

## Submit

`POST /v1/videos` accepts multipart form data:

| Field | Meaning |
| --- | --- |
| `video` | Required video upload, up to 512 MB |
| `subtitles` | UTF-8 SRT/VTT file up to 2 MB; supply this or narration text |
| `narration` | Complete narration text, up to 100,000 characters; supply this or subtitles |
| `profile` | Optional configured profile name; defaults to `default`, or the first configured name |
| `voice_sample` | Optional recording up to 25 MB, only when the selected profile declares reference-audio support |
| `voice_sample_text` | Optional transcript of that recording, up to 20,000 characters; required when the profile declares it necessary |

Exactly one nonempty narration source is required. A voice-sample transcript alone is rejected. Reference recording support is capability-gated; the service never silently ignores a recording for a preset-only profile. Uploaded filenames are not used as storage paths. Total multipart bytes and individual files are bounded; oversized data receives HTTP 413. Invalid source combinations or unknown profiles receive HTTP 422.

Reference audio and its transcript can be supplied per upload; they do not need to be stored in the server profile. The API checks declared capabilities and any transcript requirement, while backend preflight inspects configured dependencies. It does not attempt to decode a compressed MP3/M4A/OGG recording as WAV before the facade can normalize it. The product facade owns normalization and actual reference validation before synthesis.

```sh
curl -F video=@short.mp4 -F subtitles=@captions.srt \
  -F profile=default http://127.0.0.1:8765/v1/videos

curl -F video=@short.mp4 -F 'narration=Here is the complete explanation.' \
  http://127.0.0.1:8765/v1/videos
```

Accepted uploads return HTTP 202:

```json
{
  "id": "<generated job identifier>",
  "status": "queued",
  "profile": "default",
  "status_url": "/v1/videos/<id>",
  "download_url": "/v1/videos/<id>/video"
}
```

Timestamps are also returned. A queued upload is not a completed voiced video.

## Poll and download

`GET /v1/videos/{id}` returns the public record and, on success, a compact result containing verified video hash, download URL and safe production summary. It does not expose upload/config/model paths, full server receipts or raw logs.

States are `queued`, `running`, `succeeded`, `needs_retime`, `blocked` and `failed`. A `needs_retime` result explains that speech does not fit the supplied cue windows; submit a revised edit/timing rather than silently truncating words. A blocked profile or interrupted job needs a deliberate new submission. Errors include a useful public code/message; complete technical details stay in the local job record and server log.

`GET /v1/videos/{id}/video` downloads the actual completed video. It returns HTTP 409 while processing or after failure. The server rechecks the file/hash before download and blocks a changed or missing output. Downloads use generated names and a hash ETag, preserving the original upload.

## Persistence and restart

The service owns `data_dir/jobs/<id>/inputs/`, private `record.json`, `server.log`/`error.log`, and the facade's full output under `artifacts/`. These records remain local and are not returned wholesale to HTTP clients. On restart, any previously queued/running job becomes `blocked`; it is never automatically rendered again. Submit a new upload to retry manually. Completed outputs remain downloadable when their hashes still match.

The API is a local single-process service. It adds no accounts, external hosting, publication or background scheduler. The selected configured backend determines actual voice/reference behavior. Human listening review remains separate from successful technical production.

## Verification

`tests/test_api.py` uses FastAPI TestClient and an injected fixture renderer for CPU-only multipart lifecycle, file bounds, safe filenames/paths, capability gating, serial execution, restart reconciliation, errors and download integrity. Those fixture bytes are not real media or a claimed voice generation test. Run actual provider synthesis and media playback checks in the target environment before deployment.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [file uploads](https://fastapi.tiangolo.com/tutorial/request-files/) and [testing](https://fastapi.tiangolo.com/tutorial/testing/), checked 2026-11-18.

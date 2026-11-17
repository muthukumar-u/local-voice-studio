# Local Voice Studio workspace instructions

## Start here

Read `README.md` and `ops/STATE.md`, then the affected request, profile, source and tests. This repository owns local speech generation, model provenance/cache policy, timing fit, audio finishing, delivery verification and its own version history. Client content repositories own their topics, narration text and silent-video edits.

## Responsibilities

- The maintainer integrates source/configuration changes and records support status in `ops/STATE.md` and substantive changes in `CHANGELOG.md`.
- Speech/profile work owns `config/`, pinned model identities and synthesis behavior. Preserve complete words; timing overflow returns `needs_retime` rather than truncating speech or secretly changing the video.
- Delivery/QA work owns waveform/media checks, loudness finishing and full receipts under this repository's ignored `outputs/`.
- An optional liaison interprets a client's compact job request/result and routes issues to the relevant owner. It does not duplicate the model, installation, logs or full receipts inside the client repository. Use one working agent for routine work; delegate only under applicable explicit authorization and bounded file ownership.

## Skills and integration

Use `skills/voice-production/SKILL.md` for operating or diagnosing this module. It is a versioned local workflow read by path; do not change global client settings to install it.

Use the Python facade / process CLI for video+narration or video+SRT/VTT; use `docs/HTTP-API.md` for upload clients. These interfaces do not require a channel layout. Configured `[provider].python` isolates optional model stacks for whole jobs; preserve the virtualenv executable path when invoking it. Use `docs/JOB-PROTOCOL.md` for legacy client integration. Invoke the repository interpreter explicitly: `<voice-repo>/.venv/bin/python -m local_voice_studio job --request <absolute-request>`. Relative profile paths resolve within this repository. Requests bind video, sidecar, every referenced text asset and profile by SHA-256. Full jobs/logs/model details remain here; clients retain compact requests/results and artifact references.

Completed matching retries revalidate inputs and outputs before reuse. A conflicting, incomplete, blocked or failed job cannot be silently overwritten or automatically retried. Use an explicit new attempt nonce and new job ID when a new attempt is intended.

## Setup, diagnosis and state

Keep `.venv`, `.tools`, `.cache`, model files, jobs and large audio/video outputs ignored. Use the project-owned setup and pinned artifact manifests; never rewrite global Python, shell or model settings. A copied source bundle does not contain an installed runtime. Installed virtual environments are not blindly relocatable; recreate only a positively identified project-owned environment as documented.

When a user reports voice failure, identify the actual command/error, selected file or playback symptom. Test the CLI from the reported working directory, verify requested input/output hashes and inspect actual media/audio as appropriate. A successful model preflight or audio-stream header does not establish that the user's workflow or listening issue is resolved. Keep the unresolved symptom visible until evidence addresses it.

Keep credentials/private account information out of source, profiles, requests and chat output. Local development does not grant permission for remote publication, uploads or account changes.

## Verification and version control

Run `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v` for implementation changes and validate modified local skills with the bundled validator when available. Exercise substantive helper changes, including failure behavior, in temporary fixtures. Use actual cache-only synthesis/media checks when the change concerns runtime behavior; do not substitute mocked audio for delivered narration.

Inspect source-only staged changes and ignored paths before a local commit. Record decisions and limitations concisely. Do not commit or push without the current task's authorization.

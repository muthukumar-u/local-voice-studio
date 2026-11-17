# Current handoff

Updated: 2026-11-17

- Location: working staging repository is `/private/tmp/local-voice-studio`. Intended durable sibling under `/Users/muthu/Programs/personal/` is still absent; its parent is not writable in this session. No workaround or global configuration change performed.
- Runtime: project-owned CPython 3.12.16, Kokoro 0.9.4, pinned verified Kokoro-82M/af_heart assets, English G2P and bundled media tools. Explicit CLI invocation works from `/private/tmp`; unqualified `voice-studio` is not on PATH.
- User support: creator reports “voice is not working.” This remains unresolved pending their actual command/error, selected file or playback symptom. Cache-only preflight succeeds and generates 3.9 seconds of nonzero diagnostic audio; that alone does not resolve the report.
- Existing ES-001 output: levelled H.264/AAC portrait video and raw WAV exist and match their receipts; 122 seconds, 1080×1920, AAC mono 48 kHz. Human listening review remains pending.
- Interface: protocol-v1 `job --request` validates video/sidecar/profile/text snapshots, reserves immutable job records, renders with cached assets by default, and returns compact JSON. Completed matching reuse verifies current sources and outputs. Dry-run passed from an arbitrary cwd without reserving a job; the coordinator's actual channel bridge job `tech-distiller-ES-001-02dc7d3f8a1cb60d2f94` subsequently succeeded with 122-second 1080×1920 video and audio.
- Ownership: model/cache/setup/locks/full receipts/jobs/support/version details live here. Channel keeps a thin bridge plus compact requests/results and artifact references. Historical setup/synthesis/finishing/timing reports were moved to `records/tech-distiller/ES-001/`; the former channel voice guide is archived under `records/tech-distiller/`.
- Verification: 39 behavior tests and the local skill validator pass, including idempotent retries, stale narration snapshots, tampered outputs, conflicting IDs, dry-run and timing failures. Actual cache-only synthesis, existing media checks and the new channel-bridge job succeeded. The unspecified listening/playback symptom remains distinct from those checks.
- Next: complete durable-location handoff when access permits; reproduce the reported symptom; source and migrated records have been reviewed for local version history. No remote or push configured by this work.

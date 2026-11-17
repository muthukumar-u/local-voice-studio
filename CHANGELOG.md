# Changelog

Source changes are recorded here; runtime job records live in ignored `outputs/jobs/`. No release tag or remote publication is implied.

## 0.2.1 — 2026-11-18

- Remove consumer-specific records, personal paths and machine/session handoff notes from tracked source and documentation.
- Document fresh-clone setup through model preparation and upload; use generic examples and portable temporary paths in tests.
- Accept application-neutral item identifiers in optional jobs while preserving the previous identifier alias.
- Add runnable narration/SRT/VTT examples and ignore upload, sample, log, job, model and generated-media storage.

## 0.2.0 — 2026-11-18

- Add application-neutral video+narration and video+SRT/VTT production, real media probing, caption burning, complete-word fitting and verified MP4/WAV/receipt outputs.
- Add Python facade, `process` CLI, local upload page, serialized multipart HTTP jobs, status/download routes, OpenAPI schema and configured browser origins.
- Route configured local Kokoro and compatible speech endpoints through provider adapters. Add optional Qwen3-TTS Base reference conditioning, compressed sample normalization and complete-job isolated interpreter dispatch.
- Separate API and clone environment setup from the working Kokoro runtime. Clone setup checks Metal GPU availability before model installation.
- Verify real Kokoro video production and actual ASGI upload/download in both modes, sample-note normalization, isolated dispatch and distributable UI packaging. Document provider and runtime requirements.

## 0.1.0 — 2026-11-17

- Add protocol-v1 hash-bound client jobs with compact request/result exchange, cached-render defaults, immutable job reservations, source/output revalidation and verified idempotent completed reuse.
- Add no-reservation job dry-run and explicit `blocked`, `failed` and `needs_retime` outcomes; no automatic retry or clobber of an incomplete/failed job.
- Establish module-owned agent guidance, operating skill and support handoff. Keep provider readiness distinct from successful speech and media verification.

## Initial local implementation — 2026-11-17

- Build an independent Kokoro voice project with owned Python/environment/cache, pinned verified model assets, dependency/source hashes and reproducible setup.
- Generate complete cue-timed narration and H.264/AAC video; preserve raw WAV and existing silent-video inputs.
- Add bounded fit retries with honest timing overflow, optional delivery loudness finishing and receipt-based remux without re-synthesis or video re-encoding.
- Verify scheduled speech and measured delivery loudness, retaining listening review as a separate step.

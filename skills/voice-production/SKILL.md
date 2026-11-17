---
name: voice-production
description: Produce and diagnose voiced videos from narration text, SRT/VTT uploads, or existing client jobs using configured voice providers and optional reference recordings.
---

Read the repository `AGENTS.md`, `ops/STATE.md`, affected profile/request and `docs/JOB-PROTOCOL.md`. This module owns model/setup/cache/locks, jobs/logs and full receipts. Preserve client narration and source videos; keep client repositories limited to the compact exchange and delivered artifact references.

For an application-neutral input, use `product.produce_video`, `process --video ... --text ...` / `--narration ...` / `--subtitles ...`, or the local upload API. Read `README.md` for these inputs and `docs/HTTP-API.md` for HTTP integration; they do not require channel episode files or sidecars. Narration is supplied text or the subtitle cues, not guessed from pixels. Preserve dimensions/timing, burn supplied captions, and return actual output hashes. Original audio is replaced by the generated track.

For model selection or a reference sample, read `docs/PROVIDERS.md` and the selected profile. Reject references when the adapter cannot clone. Normalize compressed voice recordings privately, preserve the reference/transcript hashes, and use the configured isolated interpreter to avoid model dependency conflicts. Report missing cache, inaccessible GPU or an untested reference honestly. Setup/configuration and mock tests do not establish speaker similarity.

For a legacy client job, use this repository's explicit `.venv/bin/python -m local_voice_studio job --request <absolute-file>` entry point. Validate source/profile hashes, expectations and every sidecar-referenced text snapshot. Use `--dry-run` for a plan without reserving a job or generating speech. Cached assets are the default. Kokoro uses its verified pinned model manifest; other provider/runtime preparation is documented in docs/PROVIDERS.md.

Return the compact result honestly. Reuse a completed matching job only after source/output verification. Do not overwrite a conflicting/failed/incomplete job or loop retries; a new attempt needs an explicit nonce and new job ID. Timing overflow requires a revised client edit/caption schedule or narration, never silent word truncation.

For “voice not working,” obtain or inspect the actual failed command, file or playback symptom. Check cwd-independent invocation, environment location, hashes, cache-only model synthesis and actual media/audio where relevant. Do not treat a passing doctor or audio-header check as proof that the user's reported issue is fixed. Record what was reproduced, what remains unknown and the next useful check in `ops/STATE.md`.

Keep raw narration and delivery audio processing distinct. Report measured finishing targets/results as editorial choices, preserve source receipts, and leave human listening review pending until it occurs. For implementation changes, run the meaningful module tests, inspect source-only Git changes and update `CHANGELOG.md`. Follow existing authorization for commits, remote actions and account access; this skill grants none.

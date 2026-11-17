---
name: voice-production
description: Operate, diagnose, or maintain this local voice module from hash-bound client requests, including synthesis, cue fit, audio finishing, media verification, and compact result handoff.
---

Read the repository `AGENTS.md`, `ops/STATE.md`, affected profile/request and `docs/JOB-PROTOCOL.md`. This module owns model/setup/cache/locks, jobs/logs and full receipts. Preserve client narration and source videos; keep client repositories limited to the compact exchange and delivered artifact references.

For a client job, use this repository's explicit `.venv/bin/python -m local_voice_studio job --request <absolute-file>` entry point. Validate source/profile hashes, expectations and every sidecar-referenced text snapshot. Use `--dry-run` for a plan without reserving a job or generating speech. Cached assets are the default; any permitted downloads stay bound to the verified model manifest.

Return the compact result honestly. Reuse a completed matching job only after source/output verification. Do not overwrite a conflicting/failed/incomplete job or loop retries; a new attempt needs an explicit nonce and new job ID. Timing overflow requires a revised client edit/caption schedule or narration, never silent word truncation.

For “voice not working,” obtain or inspect the actual failed command, file or playback symptom. Check cwd-independent invocation, environment location, hashes, cache-only model synthesis and actual media/audio where relevant. Do not treat a passing doctor or audio-header check as proof that the user's reported issue is fixed. Record what was reproduced, what remains unknown and the next useful check in `ops/STATE.md`.

Keep raw narration and delivery audio processing distinct. Report measured finishing targets/results as editorial choices, preserve source receipts, and leave human listening review pending until it occurs. For implementation changes, run the meaningful module tests, inspect source-only Git changes and update `CHANGELOG.md`. Follow existing authorization for commits, remote actions and account access; this skill grants none.

# Changelog

Source changes are recorded here; runtime job records live in ignored `outputs/jobs/`. No release tag or remote publication is implied.

## Unreleased — 2026-11-17

- Add protocol-v1 hash-bound client jobs with compact request/result exchange, cached-render defaults, immutable job reservations, source/output revalidation and verified idempotent completed reuse.
- Add no-reservation job dry-run and explicit `blocked`, `failed` and `needs_retime` outcomes; no automatic retry or clobber of an incomplete/failed job.
- Establish module-owned agent guidance, operating skill and support handoff. Record the current durability/PATH gaps and the unresolved user-reported voice symptom separately from successful runtime checks.

## Initial local implementation — 2026-11-17

- Build an independent Kokoro voice project with owned Python/environment/cache, pinned verified model assets, dependency/source hashes and reproducible setup.
- Generate complete cue-timed narration and H.264/AAC video; preserve raw WAV and existing silent-video inputs.
- Add bounded fit retries with honest timing overflow, optional delivery loudness finishing and receipt-based remux without re-synthesis or video re-encoding.
- Verify the 122-second portrait episode at natural 0.95 speed and measure finished delivery loudness. Keep human listening review explicit.

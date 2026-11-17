"""Application-neutral video + narration/subtitles -> voiced MP4 facade."""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import threading

from .core import (Clip, Plan, StudioError, TimingOverflow, assemble_track, executable,
                   load_config, measure_loudness, mux_arguments, number, probe_video,
                   read_srt, required_text, sha256, validate_clips, verify_plan_unchanged,
                   write_wav)

_MODEL_LOCK = threading.Lock()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def probe_input(video, config):
    tool = executable(config.ffprobe)
    if not tool or not executable(config.ffmpeg):
        raise StudioError("FFmpeg and ffprobe are required for video production")
    result = subprocess.run([tool, "-v", "error", "-select_streams", "v:0", "-show_streams",
                             "-show_format", "-of", "json", str(video)], capture_output=True, text=True)
    try:
        stream = json.loads(result.stdout)["streams"][0]
        data = json.loads(result.stdout)
        if result.returncode or stream.get("disposition", {}).get("attached_pic"):
            raise ValueError("No moving-video stream")
        duration = number(float(stream.get("duration", data["format"]["duration"])), "video duration", 0, True)
        numerator, denominator = stream["r_frame_rate"].split("/")
        fps = number(float(numerator) / float(denominator), "video fps", 0, True)
        width, height = int(stream["width"]), int(stream["height"])
        rotation = float(stream.get("tags", {}).get("rotate", 0))
        for item in stream.get("side_data_list", []):
            if "rotation" in item:
                rotation = float(item["rotation"])
        if round(rotation) % 180 in (90,):
            width, height = height, width
        if min(width, height) < 2 or width % 2 or height % 2:
            raise StudioError("H264 output requires positive, even video dimensions")
        return {"width": width, "height": height, "fps": fps, "duration": duration}
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
        raise StudioError("Cannot probe a valid video stream; supply a playable video file") from exc


def timestamp(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def srt_text(clips):
    return "\n\n".join(f"{i}\n{timestamp(c.start)} --> {timestamp(c.end)}\n{c.text}"
                         for i, c in enumerate(clips, 1)) + "\n"


def vtt_seconds(value):
    if re.fullmatch(r"[0-5]\d:[0-5]\d\.\d{3}", value):
        value = "00:" + value
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})", value)
    if not match:
        raise StudioError(f"Invalid WebVTT timestamp: {value}")
    h, m, s, ms = map(int, match.groups())
    return 3600*h + 60*m + s + ms/1000


def read_subtitles(path, duration):
    if path.suffix.lower() == ".srt":
        return read_srt(path, duration)
    if path.suffix.lower() != ".vtt":
        raise StudioError("Subtitles must be plain-text .srt or .vtt")
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    if not blocks or not blocks[0].splitlines()[0].startswith("WEBVTT"):
        raise StudioError("WebVTT must begin with WEBVTT")
    clips = []
    for block in blocks[1:]:
        lines = block.splitlines()
        if lines[0].startswith("NOTE"):
            continue
        if lines[0] in ("STYLE", "REGION"):
            raise StudioError("Styled/positioned WebVTT is unsupported; export plain-text cues")
        if "-->" not in lines[0]:
            lines = lines[1:]  # Optional WebVTT cue identifier.
        if len(lines) < 2:
            raise StudioError("Malformed WebVTT cue")
        times = re.fullmatch(r"(\S+)\s+-->\s+(\S+)\s*", lines[0])
        if not times:
            raise StudioError("Unsupported WebVTT cue settings; export plain timestamp/text cues")
        caption = required_text(" ".join(lines[1:]), "caption text")
        if re.search(r"<[^>]+>|\{\\", caption):
            raise StudioError("Caption markup is unsupported; export plain-text cues")
        clips.append(Clip(f"C{len(clips)+1:04}", vtt_seconds(times[1]), vtt_seconds(times[2]), caption))
    validate_clips(clips, duration, "caption")
    return clips


def artifact(path):
    if not path.is_file() or not path.stat().st_size:
        raise StudioError(f"Missing or empty output: {path.name}")
    return {"path": str(path), "sha256": sha256(path)}


def normalize_reference(reference_audio, inputs, settings):
    # Mobile voice notes and common compressed recordings become a model-ready
    # private WAV; reject long samples instead of silently trimming their words.
    if reference_audio.stat().st_size > 32 * 1024 * 1024:
        raise StudioError("Reference recording exceeds 32 MiB; provide a shorter sample")
    normalized = inputs / "reference.wav"
    probe = subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type:format=duration", "-of", "json",
                            str(reference_audio)], capture_output=True, text=True)
    try:
        metadata_audio = json.loads(probe.stdout)
        seconds = float(metadata_audio["format"]["duration"])
        if probe.returncode or not metadata_audio["streams"] or not 0 < seconds <= 60:
            raise ValueError("Reference must have 0–60 seconds of audio")
    except (KeyError, ValueError, TypeError) as exc:
        raise StudioError("Reference needs readable audio lasting at most 60 seconds; trim it with a matching transcript") from exc
    converted = subprocess.run([settings.ffmpeg, "-v", "error", "-n", "-i", str(reference_audio),
                                "-vn", "-ac", "1", "-ar", str(settings.sample_rate), "-c:a", "pcm_s16le",
                                str(normalized)], capture_output=True, text=True)
    if converted.returncode:
        raise StudioError("Could not normalize the reference voice recording")
    return normalized


def produce_video(video: Path, config: Path, output_dir: Path, narration: str | None = None,
                  subtitles: Path | None = None, reference_audio: Path | None = None,
                  reference_text: str | None = None, *, dry_run=False, _worker=False) -> dict:
    """Produce one MP4; raises StudioError/TimingOverflow, never overwrites a run.

    Narration is a literal script, not inferred from pixels. Supplied subtitle text
    becomes speech and burned captions. The original video's audio is replaced.
    References are private local input and only accepted by a capable provider.
    """
    if (narration is None) == (subtitles is None):
        raise StudioError("Supply exactly one of narration text or a subtitle file")
    video, config, output_dir = (Path(p).expanduser().resolve() for p in (video, config, output_dir))
    if not video.is_file():
        raise StudioError("Video file does not exist")
    settings = load_config(config)
    if getattr(settings, "provider_python", "") and not _worker:
        from .runtime_worker import delegate
        return delegate(settings.provider_python, {
            "video": str(video), "config": str(config), "output_dir": str(output_dir),
            "narration": narration, "subtitles": str(Path(subtitles).expanduser().resolve()) if subtitles else None,
            "reference_audio": str(Path(reference_audio).expanduser().resolve()) if reference_audio else None,
            "reference_text": reference_text, "dry_run": dry_run,
        }, timeout=max(600, settings.provider_timeout_seconds))
    if _worker and hasattr(settings, "provider_python"):
        settings = replace(settings, provider_python="")
    metadata = probe_input(video, settings)
    snapshots = {str(video): sha256(video), str(config): sha256(config)}
    if subtitles is not None:
        subtitles = Path(subtitles).expanduser().resolve()
        try:
            clips = read_subtitles(subtitles, metadata["duration"])
        except (OSError, UnicodeError) as exc:
            raise StudioError("Subtitles must be a readable UTF-8 SRT/VTT file") from exc
        snapshots[str(subtitles)] = sha256(subtitles)
    else:
        clips = [Clip("N0001", 0, metadata["duration"], required_text(narration, "narration"))]
    # Configured references and request overrides follow the same provenance rule.
    reference_audio = Path(reference_audio).expanduser().resolve() if reference_audio else getattr(settings, "reference_audio", None)
    reference_text = reference_text if reference_text is not None else getattr(settings, "reference_text", None)
    if reference_audio:
        snapshots[str(reference_audio)] = sha256(reference_audio)
    if reference_text and not reference_audio:
        raise StudioError("A reference transcript needs a reference audio sample")
    from .providers import backend_status, create_backend
    status = backend_status(settings)
    if reference_audio and not status["capabilities"].get("cloning"):
        raise StudioError("Selected provider does not support sample-based voice cloning; choose a cloning profile")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise StudioError("Output directory is not empty; choose a new directory, existing work was preserved")
    planned = {"schema_version": 1, "status": "planned", "mode": "subtitles" if subtitles else "narration",
               "summary": {**metadata, "audio_present": False, "voice_generated": False,
                           "subtitles_burned": False}, "outputs": {}, "dry_run": True}
    if dry_run:
        return planned
    output_dir.mkdir(parents=True, exist_ok=True)
    inputs = output_dir / "inputs"
    inputs.mkdir()
    caption_path = inputs / "captions.srt" if subtitles else None
    if caption_path:
        caption_path.write_text(srt_text(clips))
    script = inputs / "narration.txt"
    script.write_text("\n\n".join(c.text for c in clips) + "\n")
    snapshots[str(script)] = sha256(script)
    if reference_text:
        transcript = inputs / "reference.txt"
        transcript.write_text(reference_text)
        snapshots[str(transcript)] = sha256(transcript)
    if caption_path:
        snapshots[str(caption_path)] = sha256(caption_path)
    sidecar = inputs / "plan.json"
    write_json(sidecar, {"video": str(video), **metadata, "clips": [asdict(c) for c in clips]})
    snapshots[str(sidecar)] = sha256(sidecar)
    plan = Plan(video, sidecar, snapshots[str(video)], **metadata, clips=clips, segments=clips,
                captions_path=caption_path, timeline_path=None, target={},
                fit_mode="cue" if subtitles else "scene", inputs=snapshots)
    receipt_path = output_dir / "receipt.json"
    receipt = {"schema_version": 1, "status": "started", "created_at": datetime.now(timezone.utc).isoformat(),
               "mode": planned["mode"], "inputs_sha256": snapshots, "engine": settings.engine,
               "model": settings.model, "metadata": metadata, "original_audio": "replaced",
               "reference_audio": str(reference_audio) if reference_audio else None,
               "reference_transcript_supplied": bool(reference_text), "listening_approved": False,
               "caption_alignment": "supplied_cue_windows" if subtitles else "single_passage_not_word_aligned"}
    write_json(receipt_path, receipt)
    try:
        if reference_audio:
            reference_audio = normalize_reference(reference_audio, inputs, settings)
            snapshots[str(reference_audio)] = sha256(reference_audio)
            receipt["normalized_reference"] = str(reference_audio)
        with _MODEL_LOCK, redirect_stdout(sys.stderr):
            backend = create_backend(settings, allow_downloads=False, reference_audio=reference_audio,
                                     reference_text=reference_text)
            track, rendered, gain = assemble_track(plan, settings, backend.synthesize)
        verify_plan_unchanged(plan)
        audio = output_dir / "narration.wav"
        write_wav(audio, track, settings.sample_rate, settings.peak_limit)
        final = output_dir / "video.mp4"
        settings = replace(settings, video_mode="h264")
        arguments = mux_arguments(plan, settings, audio, final)
        if subtitles:
            arguments[-1:-1] = ["-vf", "subtitles=filename=captions.srt:force_style='FontName=Arial,FontSize=9,MarginV=23,MarginL=20,MarginR=20,Outline=0.6,Shadow=0.2'"]
        muxed = subprocess.run(arguments, cwd=inputs, capture_output=True, text=True)
        if muxed.returncode:
            raise StudioError(f"Video encoding failed: {muxed.stderr[-2000:]}")
        verify_plan_unchanged(plan)
        probe = probe_video(replace(plan, video=final, digest=sha256(final)), settings)
        from .jobs import probe_audio, verify_wave
        verify_wave(audio, plan.duration)
        audio_present = probe_audio(final, settings)
        receipt.update(status="succeeded", model_provenance=backend.provenance,
                       reference_used=bool(reference_audio), clips=[details for _, _, details in rendered],
                       gain=gain, subtitles_burned=bool(subtitles), output_probe=probe,
                       measured_delivery=measure_loudness(final, settings), ffmpeg_command=arguments)
        write_json(receipt_path, receipt)
        outputs = {"video": artifact(final), "narration": artifact(audio), "receipt": artifact(receipt_path)}
        if caption_path:
            outputs["subtitles"] = artifact(caption_path)
        result = {"schema_version": 1, "status": "succeeded", "outputs": outputs,
                  "summary": {**probe, "audio_present": audio_present, "voice_generated": True,
                              "subtitles_burned": bool(subtitles)}}
        write_json(output_dir / "result.json", result)
        return result
    except Exception as exc:
        receipt.update(status="needs_retime" if isinstance(exc, TimingOverflow) else "failed", error=str(exc))
        if isinstance(exc, TimingOverflow):
            receipt["retime_plan"] = exc.details
        write_json(receipt_path, receipt)
        if isinstance(exc, (StudioError, OSError)):
            raise
        raise StudioError(f"Video production failed: {exc}") from exc

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from . import __version__
from .core import (StudioError, TimingOverflow, assemble_track, build_plan, environment_status,
                   load_config, mux_video, probe_video, sha256, validated_samples,
                   verify_plan_unchanged, write_wav, read_json, Plan, loudness_targets, measure_loudness, video_stream_hash)


def default_config():
    candidate = Path(__file__).resolve().parents[2] / "config" / "explainer.toml"
    return candidate if candidate.is_file() else None


def parser():
    root = argparse.ArgumentParser(description="Add timed local Kokoro narration to an agent-generated video and its sidecar.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("doctor", "prepare", "plan", "render", "finish", "job", "process", "serve"):
        item = commands.add_parser(name)
        if name!="job":
            item.add_argument("--config", type=Path, default=default_config())
        if name == "job":
            item.add_argument("--request",type=Path,required=True)
            item.add_argument("--dry-run",action="store_true")
        if name in ("plan", "render"):
            item.add_argument("--video", type=Path, required=True)
            item.add_argument("--sidecar", type=Path)
        if name in ("doctor", "render"):
            item.add_argument("--audio-only", action="store_true", help="Do not require or run FFmpeg muxing")
        if name == "doctor":
            item.add_argument("--model-preflight", action="store_true", help="Load the model and synthesize an in-memory diagnostic phrase")
            item.add_argument("--no-download", action="store_true", default=True, help="Preflight is cache-only; missing assets fail without download")
        if name == "render":
            item.add_argument("--dry-run", action="store_true", help="Validate and print a plan without importing model dependencies")
            item.add_argument("--output-dir", type=Path)
            item.add_argument("--allow-downloads", action="store_true", help="Permit model/voice asset downloads into the configured local cache")
        if name == "finish":
            item.add_argument("--receipt",type=Path,required=True,help="Completed synthesis/video receipt to reuse without generating speech again")
            item.add_argument("--output-dir",type=Path,required=True)
    return root


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def render(plan, config, args):
    from .providers import create_backend
    if not args.audio_only:
        actual_probe = probe_video(plan, config)
        if not environment_status(config)["ffmpeg"]:
            raise StudioError("ffmpeg missing; use --audio-only for narration WAV only")
    else:
        actual_probe = "not_checked_audio_only; sidecar timing and video identity verified"
    folder = args.output_dir.expanduser().resolve() if args.output_dir else config.outputs / f"{plan.video.stem}-{plan.digest[:8]}"
    if folder.exists() and any(folder.iterdir()):
        raise StudioError(f"Output directory is not empty: {folder}. Choose a new --output-dir; no files were overwritten")
    with redirect_stdout(sys.stderr):
        backend = create_backend(config, allow_downloads=args.allow_downloads)
    verify_plan_unchanged(plan)
    folder.mkdir(parents=True, exist_ok=True)
    receipt = {"schema_version": 1, "tool_version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
               "status": "started", "plan": plan.to_dict(), "voice": config.voice, "model": config.model,
               "language": config.language, "device": config.device, "sample_rate": config.sample_rate,
               "config_path": str(args.config.resolve()) if args.config else None,
               "config_sha256": sha256(args.config.resolve()) if args.config else None,
               "model_provenance": backend.provenance,
               "video_probe": actual_probe, "voice_quality_review": "not_performed; audition required",
               "caption_alignment": "cue_scheduled_not_word_aligned" if config.fit_mode == "cue" else "requires_recheck"}
    receipt_path = folder / "receipt.json"
    try:
        with redirect_stdout(sys.stderr):
            track, clips, gain = assemble_track(plan, config, backend.synthesize)
        verify_plan_unchanged(plan)
        clip_folder = folder / "clips"; clip_folder.mkdir()
        for index, (clip, samples, details) in enumerate(clips, 1):
            write_wav(clip_folder / f"{index:04d}.wav", samples, config.sample_rate, config.peak_limit)
        audio = folder / "narration.wav"
        write_wav(audio, track, config.sample_rate, config.peak_limit)
        receipt.update(status="audio_complete", audio=str(audio), audio_sha256=sha256(audio),
                       duration=len(track)/config.sample_rate, gain=gain,
                       clips=[details for _, _, details in clips])
        receipt["audio_finishing"]={"targets":loudness_targets(config),"applied_to_video":False,"raw_wav_loudness_normalized":False}
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+"\n")
        if not args.audio_only:
            suffix = ".mp4" if config.video_mode == "h264" else ".mov"
            destination = folder / (plan.video.stem + "_voiced" + suffix)
            receipt["ffmpeg_command"] = mux_video(plan, config, audio, destination)
            result_plan = type(plan)(destination, plan.sidecar, sha256(destination), plan.duration, plan.width, plan.height,
                                     plan.fps, plan.clips, plan.segments, plan.captions_path, plan.timeline_path, plan.target, plan.fit_mode, plan.inputs)
            receipt["output_video_probe"] = probe_video(result_plan, config)
            receipt["audio_finishing"]["applied_to_video"]=config.loudness_lufs is not None
            if config.loudness_lufs is not None:
                receipt["audio_finishing"]["measured_delivery"]=measure_loudness(destination,config)
            receipt.update(status="video_complete", video=str(destination), video_sha256=sha256(destination))
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+"\n")
        return receipt
    except TimingOverflow as exc:
        receipt.update(status="timing_overflow", error=str(exc), retime_plan=exc.details)
        (folder / "retime-plan.json").write_text(json.dumps(exc.details, indent=2, ensure_ascii=False)+"\n")
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+"\n")
        raise
    except Exception as exc:
        receipt.update(status="failed", error=str(exc))
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+"\n")
        raise


def finish(config,args):
    source_path=args.receipt.expanduser().resolve()
    source=read_json(source_path)
    if source.get("status")!="video_complete":
        raise StudioError("finish requires a completed video receipt")
    audio=Path(source.get("audio","")).expanduser().resolve()
    video=Path(source.get("video","")).expanduser().resolve()
    if sha256(audio)!=source.get("audio_sha256") or sha256(video)!=source.get("video_sha256"):
        raise StudioError("Existing narration/video hash differs from its source receipt")
    source_receipt_hash=sha256(source_path)
    metadata=source.get("output_video_probe",{})
    try:
        plan=Plan(video,source_path,source["video_sha256"],float(metadata["duration"]),int(metadata["width"]),int(metadata["height"]),float(metadata["fps"]),[],[],None,None,{},"cue",{})
    except (KeyError,ValueError,TypeError) as exc:
        raise StudioError("Source receipt lacks valid completed-video metadata") from exc
    probe_video(plan,config)
    # This finish path copies the already validated H264 stream to preserve every frame.
    ffprobe=environment_status(config)["ffprobe"]
    import subprocess
    codec_result=subprocess.run([ffprobe,"-v","error","-select_streams","v:0","-show_entries","stream=codec_name","-of","default=noprint_wrappers=1:nokey=1",str(video)],capture_output=True,text=True)
    if codec_result.returncode:
        raise StudioError(f"Cannot inspect source codec: {codec_result.stderr.strip()}")
    codec=codec_result.stdout.strip()
    if codec!="h264":
        raise StudioError("finish copies H264 video only; use normal render for another source codec")
    folder=args.output_dir.expanduser().resolve()
    if folder.exists() and any(folder.iterdir()):
        raise StudioError(f"Output directory is not empty: {folder}; source artifacts were not changed")
    folder.mkdir(parents=True,exist_ok=True)
    destination=folder/(video.stem+"_levelled.mp4")
    config_hash=sha256(args.config.resolve()) if args.config else None
    command=mux_video(plan,config,audio,destination,copy_video=True)
    if sha256(audio)!=source["audio_sha256"] or sha256(video)!=source["video_sha256"] or sha256(source_path)!=source_receipt_hash:
        raise StudioError("Finishing source changed during processing; do not promote the output")
    if args.config and sha256(args.config.resolve())!=config_hash:
        raise StudioError("Finishing config changed during processing; do not promote the output")
    final_hash=sha256(destination)
    output_probe=probe_video(replace(plan,video=destination,digest=final_hash),config)
    source_stream_hash=video_stream_hash(video,config)
    delivery_stream_hash=video_stream_hash(destination,config)
    if source_stream_hash!=delivery_stream_hash:
        raise StudioError("Audio finishing changed the encoded video stream; do not promote this output")
    measured=measure_loudness(destination,config)
    before=measure_loudness(audio,config)
    receipt={**source,"created_at":datetime.now(timezone.utc).isoformat(),"processing_stage":"audio_finishing",
             "source_receipt":{"path":str(source_path),"sha256":source_receipt_hash},
             "source_video":{"path":str(video),"sha256":source["video_sha256"],"stream_processing":"copy; no video re-encoding","encoded_stream_sha256":source_stream_hash},
             "synthesis_config_sha256":source.get("config_sha256"),"config_path":str(args.config.resolve()) if args.config else None,
             "config_sha256":config_hash,"plan_basis":"plan is original synthesis snapshot; current config applies only audio finishing",
             "audio_finishing":{"targets":loudness_targets(config),"applied_to_video":config.loudness_lufs is not None,
                                "raw_wav_loudness_normalized":False,"raw_wav_unchanged":True,
                                "delivery_audio_sample_rate":48000,"raw_wav_sample_rate":source.get("sample_rate"),
                                "measured_raw_narration":before,"measured_delivery":measured,
                                "speech_resynthesized":False,"timing_or_speed_changed":False},
             "video":str(destination),"video_sha256":final_hash,"output_video_probe":output_probe,"ffmpeg_command":command,
             "encoded_video_stream_sha256":delivery_stream_hash,"encoded_video_stream_unchanged":True}
    (folder/"receipt.json").write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n")
    return receipt


def main(argv=None):
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    if actual_argv and actual_argv[0] in ("process", "serve"):
        from .product_cli import main as product_main
        return product_main(actual_argv)
    args = parser().parse_args(actual_argv)
    if args.command=="job":
        from .jobs import run_job,exit_code
        result=run_job(args.request,dry_run=args.dry_run)
        emit(result);return exit_code(result)
    try:
        config = load_config(args.config)
        if args.command=="finish":
            emit(finish(config,args));return 0
        if args.command == "doctor":
            status = environment_status(config, audio_only=args.audio_only)
            if args.model_preflight:
                if status["blockers"]:
                    status["model_preflight"] = "not_attempted_missing_prerequisites"
                else:
                    from .providers import create_backend
                    try:
                        with redirect_stdout(sys.stderr):
                            backend = create_backend(config, allow_downloads=False)
                            samples = validated_samples(backend.synthesize("A clear explanation starts with one useful question.", config.speed), config.sample_rate)
                        status["model_preflight"] = "remote_synthesis_passed" if config.engine == "openai_compatible" else "cache_only_synthesis_passed"
                        status["diagnostic_audio_seconds"] = len(samples)/config.sample_rate
                        status["model_installed"] = "remote_not_locally_installed" if config.engine == "openai_compatible" else "local_model_loaded_and_nonzero_audio_generated"
                        status["ready"] = True
                        status["readiness_basis"] = "actual_remote_synthesis" if config.engine == "openai_compatible" else "actual_cache_only_synthesis"
                    except StudioError as exc:
                        status["model_preflight"] = "cache_only_synthesis_failed"
                        status["blockers"].append(str(exc)); status["ready"] = False
            emit(status)
            return 0 if not status["blockers"] else 2
        if args.command == "prepare":
            # Explicit one-time cache population; no global package installation.
            from .providers import create_backend
            with redirect_stdout(sys.stderr):
                backend = create_backend(config, allow_downloads=True)
                samples = validated_samples(backend.synthesize("A clear explanation starts with one useful question.", config.speed), config.sample_rate)
            emit({"status": "remote_synthesis_verified" if config.engine == "openai_compatible" else "model_prepared", "cache": str(config.cache), "voice": config.voice,
                  "diagnostic_audio_seconds": len(samples)/config.sample_rate,
                  "auditory_quality": "not_reviewed"})
            return 0
        plan = build_plan(args.video, config, args.sidecar)
        if args.config:
            plan.inputs[str(args.config.resolve())] = sha256(args.config.resolve())
        if args.command == "plan" or args.dry_run:
            emit(plan.to_dict()); return 0
        emit(render(plan, config, args)); return 0
    except TimingOverflow as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 3
    except (StudioError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())

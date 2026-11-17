"""Dependency-free planning, validation, timing, PCM assembly, and FFmpeg calls."""
from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass, field
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from typing import Callable
import wave


class StudioError(Exception):
    """An actionable input, environment, or production failure."""


class TimingOverflow(StudioError):
    def __init__(self, details: dict):
        self.details = details
        super().__init__(
            f"{details['id']}: speech needs {details['observed_seconds']:.3f}s, "
            f"slot allows {details['available_seconds']:.3f}s after bounded attempts "
            f"(last speed {details['last_speed']:.2f}; ceiling {details['max_speed']:.2f}). No words were truncated. "
            "Extend/re-time this cue and its video, or shorten the script."
        )


def number(value, label: str, minimum=0, strictly=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudioError(f"{label} must be a number")
    value = float(value)
    if not math.isfinite(value) or value < minimum or (strictly and value == minimum):
        raise StudioError(f"{label} must be finite and {'greater than' if strictly else 'at least'} {minimum}")
    return value


def required_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StudioError(f"{label} must be a nonempty string")
    return value.strip()


def object_value(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise StudioError(f"{label} must be an object")
    return value


def read_json(path: Path) -> dict:
    try:
        return object_value(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, ValueError) as exc:
        raise StudioError(f"Cannot read valid JSON at {path}: {exc}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise StudioError(f"Cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def normal_text(text: str) -> str:
    return " ".join(text.split())


def word_sequence(text: str) -> list[str]:
    # Ignore typography/spacing but never discard an actual word for coverage checks.
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


@dataclass
class Config:
    engine: str = "kokoro"
    model: str = "hexgrad/Kokoro-82M"
    voice: str = "af_heart"
    language: str = "a"
    device: str = "cpu"
    speed: float = 0.95
    max_speed: float = 1.15
    max_attempts: int = 3
    sample_rate: int = 24000
    fit_mode: str = "cue"
    end_padding_seconds: float = 0.04
    chunk_pause_seconds: float = 0.035
    peak_limit: float = 0.95
    cache: Path = field(default_factory=lambda: Path.cwd() / ".cache")
    outputs: Path = field(default_factory=lambda: Path.cwd() / "outputs")
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    video_mode: str = "h264"
    crf: int = 18
    loudness_lufs: float | None = None
    true_peak_db: float = -1.5
    loudness_range: float = 11.0
    pronunciation: dict[str, str] = field(default_factory=dict)
    provider_base_url: str = ""
    provider_api_key_env: str = ""
    provider_timeout_seconds: float = 60.0
    provider_python: str = ""
    reference_audio: Path | None = None
    reference_text: str | None = None


def load_config(path: Path | None) -> Config:
    config = Config()
    if path is not None:
        path = path.expanduser().resolve()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StudioError(f"Cannot read TOML config {path}: {exc}") from exc
        permitted = {"voice", "paths", "mux", "pronunciation", "provider", "reference"}
        if set(data) - permitted:
            raise StudioError(f"Unknown config sections: {sorted(set(data) - permitted)}")
        for section in ("voice", "mux"):
            allowed = {
                "voice": {"engine", "model", "voice", "language", "device", "speed", "max_speed", "max_attempts", "sample_rate", "fit_mode", "end_padding_seconds", "chunk_pause_seconds", "peak_limit"},
                "mux": {"ffmpeg", "ffprobe", "video_mode", "crf", "loudness_lufs", "true_peak_db", "loudness_range"},
            }[section]
            values = object_value(data.get(section, {}), section)
            if set(values) - allowed:
                raise StudioError(f"Unknown {section} keys: {sorted(set(values) - allowed)}")
            for key, value in values.items():
                setattr(config, key, value)
        voice_values = object_value(data.get("voice", {}), "voice")
        if config.engine == "mlx_audio":
            for key, value in {"voice": "", "language": "auto", "device": "mps", "speed": 1.0, "max_speed": 1.0}.items():
                if key not in voice_values:
                    setattr(config, key, value)
        provider = object_value(data.get("provider", {}), "provider")
        if set(provider) - {"base_url", "api_key_env", "timeout_seconds", "python"}:
            raise StudioError("Unknown provider configuration keys")
        for key, value in provider.items():
            setattr(config, "provider_" + key, value)
        if config.provider_python:
            worker_python = Path(required_text(config.provider_python, "provider.python")).expanduser()
            config.provider_python = os.path.abspath(str(path.parent/worker_python) if not worker_python.is_absolute() else str(worker_python))
        reference = object_value(data.get("reference", {}), "reference")
        if set(reference) - {"audio", "text"}:
            raise StudioError("Unknown reference configuration keys")
        if "audio" in reference:
            audio_path = Path(required_text(reference["audio"], "reference.audio")).expanduser()
            config.reference_audio = (path.parent / audio_path).resolve() if not audio_path.is_absolute() else audio_path.resolve()
        if "text" in reference:
            config.reference_text = required_text(reference["text"], "reference.text")
        paths = object_value(data.get("paths", {}), "paths")
        if set(paths) - {"cache", "outputs"}:
            raise StudioError("paths supports only cache and outputs")
        for key in ("cache", "outputs"):
            value = Path(required_text(paths.get(key, "../" + (".cache" if key == "cache" else "outputs")), f"paths.{key}")).expanduser()
            setattr(config, key, (path.parent / value).resolve() if not value.is_absolute() else value.resolve())
        for key in ("ffmpeg", "ffprobe"):
            value = getattr(config, key)
            if isinstance(value, str) and ("/" in value or "\\" in value):
                candidate = Path(value).expanduser()
                setattr(config, key, str((path.parent/candidate).resolve() if not candidate.is_absolute() else candidate.resolve()))
        config.pronunciation = object_value(data.get("pronunciation", {}), "pronunciation")
    for key in ("engine", "model", "language", "device", "fit_mode", "ffmpeg", "ffprobe", "video_mode"):
        setattr(config, key, required_text(getattr(config, key), key))
    if config.engine != "mlx_audio":
        config.voice = required_text(config.voice, "voice")
    if type(config.sample_rate) is not int or not 8000 <= config.sample_rate <= 96000:
        raise StudioError("sample_rate must be an integer from 8000 to 96000")
    if config.device not in ("cpu", "cuda", "mps"):
        raise StudioError("device must be cpu, cuda, or mps")
    config.speed = number(config.speed, "speed", 0.5)
    config.max_speed = number(config.max_speed, "max_speed", config.speed)
    if config.max_speed > 1.25:
        raise StudioError("max_speed must be <=1.25; retime rather than making rushed narration")
    if type(config.max_attempts) is not int or not 1 <= config.max_attempts <= 5:
        raise StudioError("max_attempts must be an integer from 1 to 5")
    if config.fit_mode not in ("cue", "scene"):
        raise StudioError("fit_mode must be cue or scene")
    if config.video_mode not in ("h264", "copy_mov"):
        raise StudioError("video_mode must be h264 or copy_mov")
    if type(config.crf) is not int or not 0 <= config.crf <= 35:
        raise StudioError("crf must be an integer from 0 to 35")
    for key, minimum, maximum in [("loudness_lufs",-24,-9),("true_peak_db",-9,-.1),("loudness_range",1,20)]:
        value = getattr(config,key)
        if key == "loudness_lufs" and value is None:
            continue
        value = number(value,key,minimum)
        if value > maximum:
            raise StudioError(f"{key} must be between {minimum} and {maximum}")
        setattr(config,key,value)
    for key in ("end_padding_seconds", "chunk_pause_seconds"):
        value = number(getattr(config, key), key)
        if value > 1:
            raise StudioError(f"{key} must be <=1 second")
        setattr(config, key, value)
    config.peak_limit = number(config.peak_limit, "peak_limit", 0, strictly=True)
    if config.peak_limit > 1:
        raise StudioError("peak_limit must be <=1")
    for key, value in config.pronunciation.items():
        required_text(key, "pronunciation key")
        required_text(value, "pronunciation value")
    from .providers import validate_provider_config
    validate_provider_config(config)
    return config


@dataclass
class Clip:
    id: str
    start: float
    end: float
    text: str


@dataclass
class Plan:
    video: Path
    sidecar: Path
    digest: str
    duration: float
    width: int
    height: int
    fps: float
    clips: list[Clip]
    segments: list[Clip]
    captions_path: Path | None
    timeline_path: Path | None
    target: dict
    fit_mode: str
    inputs: dict

    def to_dict(self) -> dict:
        return {
            "schema_version": 1, "video": str(self.video), "sidecar": str(self.sidecar),
            "sha256": self.digest, "duration": self.duration, "width": self.width,
            "height": self.height, "fps": self.fps, "fit_mode": self.fit_mode,
            "caption_alignment": "cue_scheduled_not_word_aligned" if self.fit_mode == "cue" else "requires_recheck",
            "clips": [asdict(clip) for clip in self.clips], "target": self.target,
            "inputs_sha256": self.inputs,
            "media_probe": "not_performed_by_plan; identity verified by sha256",
            "voice_generated": False,
        }


def local_path(base: Path, value, label: str, required=True) -> Path | None:
    if value is None and not required:
        return None
    text = required_text(value, label)
    if "://" in text:
        raise StudioError(f"{label} must be a local path, not a URL")
    path = Path(text).expanduser()
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file():
        raise StudioError(f"Missing {label}: {path}")
    return path


def validate_clips(clips: list[Clip], duration: float, label: str) -> None:
    if not clips:
        raise StudioError(f"{label} cannot be empty")
    ids = set()
    previous = 0.0
    for clip in clips:
        if clip.id in ids:
            raise StudioError(f"Duplicate {label} ID: {clip.id}")
        ids.add(clip.id)
        if clip.start < previous - 0.001 or clip.end <= clip.start or clip.end > duration + 0.001:
            raise StudioError(f"Invalid/overlapping {label} timing for {clip.id}")
        previous = clip.end


def read_segments(path: Path, duration: float) -> list[Clip]:
    data = read_json(path)
    if data.get("schema_version", 1) != 1:
        raise StudioError("Unsupported voice-segments schema_version")
    if "duration" in data and abs(number(data["duration"], "segments.duration", 0, True) - duration) > 0.05:
        raise StudioError("voice-segments duration does not match video sidecar")
    entries = data.get("segments")
    if not isinstance(entries, list):
        raise StudioError("voice-segments requires a segments list")
    clips = []
    for index, item in enumerate(entries):
        item = object_value(item, f"segments[{index}]")
        start = number(item.get("start"), f"segments[{index}].start")
        if "end" in item:
            end = number(item["end"], f"segments[{index}].end")
            if "duration" in item and abs(end-start-number(item["duration"], "segment.duration", 0, True)) > .001:
                raise StudioError("Segment end and duration disagree")
        else:
            end = start + number(item.get("duration"), f"segments[{index}].duration", 0, True)
        clips.append(Clip(required_text(item.get("id"), f"segments[{index}].id"), start, end, required_text(item.get("text"), f"segments[{index}].text")))
    validate_clips(clips, duration, "segment")
    return clips


def srt_time(value: str) -> float:
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})", value.strip())
    if not match:
        raise StudioError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def read_srt(path: Path, duration: float) -> list[Clip]:
    try:
        blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip().replace("\r\n", "\n"))
    except OSError as exc:
        raise StudioError(f"Cannot read captions: {exc}") from exc
    clips = []
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit() or " --> " not in lines[1]:
            raise StudioError(f"Malformed SRT cue {index}")
        start, end = lines[1].split(" --> ", 1)
        text = normal_text(" ".join(lines[2:]))
        if re.search(r"<[^>]+>|\{\\", text):
            raise StudioError("Caption formatting tags are not narration; export plain-text SRT")
        clips.append(Clip(f"C{index:04d}", srt_time(start), srt_time(end), required_text(text, "caption text")))
    validate_clips(clips, duration, "caption")
    return clips


def build_plan(video: Path, config: Config, sidecar: Path | None = None) -> Plan:
    video = video.expanduser().resolve()
    if not video.is_file():
        raise StudioError(f"Video does not exist: {video}")
    sidecar = sidecar.expanduser().resolve() if sidecar else video.with_suffix(".voice.json")
    if not sidecar.is_file():
        raise StudioError(f"Missing narration sidecar: {sidecar}. Export it with the video; this tool does not guess a script from silent frames.")
    data = read_json(sidecar)
    if data.get("schema_version") != 1:
        raise StudioError("Video sidecar requires schema_version=1")
    metadata = object_value(data.get("video"), "sidecar.video")
    identity_path = local_path(sidecar.parent, metadata.get("path"), "sidecar.video.path")
    if identity_path != video:
        raise StudioError("Sidecar video.path identifies a different video")
    expected = required_text(metadata.get("sha256"), "sidecar.video.sha256").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise StudioError("sidecar.video.sha256 must contain 64 hexadecimal characters")
    digest = sha256(video)
    if expected != digest:
        raise StudioError("Video SHA-256 mismatch: the sidecar describes a different edit; regenerate the sidecar")
    duration = number(metadata.get("duration"), "video.duration", 0, True)
    width = metadata.get("width"); height = metadata.get("height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise StudioError("video.width and video.height must be positive integers")
    fps = number(metadata.get("fps"), "video.fps", 0, True)
    target = object_value(data.get("target", {}), "sidecar.target")
    maximum = target.get("max_duration_seconds")
    if maximum is not None and duration > number(maximum, "target.max_duration_seconds", 0, True):
        raise StudioError("Video exceeds its declared target.max_duration_seconds")
    narration = object_value(data.get("narration"), "sidecar.narration")
    segments_path = local_path(sidecar.parent, narration.get("segments_path"), "narration.segments_path")
    captions = local_path(sidecar.parent, narration.get("captions_srt_path"), "narration.captions_srt_path", required=config.fit_mode == "cue")
    timeline = local_path(sidecar.parent, narration.get("timeline_path"), "narration.timeline_path", required=False)
    segments = read_segments(segments_path, duration)
    clips = read_srt(captions, duration) if config.fit_mode == "cue" else segments
    if config.fit_mode == "cue":
        if word_sequence(" ".join(c.text for c in clips)) != word_sequence(" ".join(c.text for c in segments)):
            raise StudioError("Caption narration does not match the voice-segments script; no words may be dropped or invented")
        for segment in segments:
            contained = [c for c in clips if c.start >= segment.start-.001 and c.end <= segment.end+.001]
            if word_sequence(" ".join(c.text for c in contained)) != word_sequence(segment.text):
                raise StudioError(f"Captions cross/mismatch scene {segment.id}; fix timing or text before synthesis")
    inputs = {str(sidecar): sha256(sidecar), str(segments_path): sha256(segments_path)}
    if captions:
        inputs[str(captions)] = sha256(captions)
    if timeline:
        timeline_data = read_json(timeline)
        scenes = timeline_data.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != len(segments):
            raise StudioError("Timeline scenes and narration segments differ")
        cursor = 0.0
        for scene, segment in zip(scenes, segments):
            scene = object_value(scene, "timeline scene")
            span = number(scene.get("duration"), "timeline scene.duration", 0, True)
            narration_text = scene.get("narration", scene.get("text"))
            if abs(cursor-segment.start) > .05 or abs(cursor+span-segment.end) > .05 or word_sequence(required_text(narration_text, "timeline scene.narration")) != word_sequence(segment.text):
                raise StudioError(f"Timeline and voice-segments differ for {segment.id}")
            cursor += span
        inputs[str(timeline)] = sha256(timeline)
    return Plan(video, sidecar, digest, duration, width, height, fps, clips, segments, captions, timeline, target, config.fit_mode, inputs)


def executable(value: str) -> str | None:
    return shutil.which(value)


def environment_status(config: Config, audio_only=False) -> dict:
    if config.engine != "kokoro":
        from .providers import backend_status
        return backend_status(config, audio_only=audio_only)
    modules = {}
    for name in ("kokoro", "numpy", "soundfile", "torch", "spacy", "en_core_web_sm"):
        try:
            modules[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            modules[name] = False
    supported = (3, 11) <= sys.version_info[:2] < (3, 13)
    ffmpeg = executable(config.ffmpeg); ffprobe = executable(config.ffprobe)
    # espeak fallback prevents quietly skipping out-of-dictionary English words.
    import ctypes.util
    espeak = executable("espeak-ng") or ctypes.util.find_library("espeak-ng")
    if not espeak:
        try:
            spec = importlib.util.find_spec("espeakng_loader")
            if spec and spec.origin:
                suffix = ".dylib" if sys.platform == "darwin" else ".dll" if sys.platform == "win32" else ".so"
                filename = "espeak-ng"+suffix if sys.platform == "win32" else "libespeak-ng"+suffix
                candidate = Path(spec.origin).parent/filename
                if candidate.is_file():
                    espeak = str(candidate)
        except (ImportError, ValueError, AttributeError):
            pass
    blockers = []
    if not supported:
        blockers.append("Use Python 3.11 or 3.12 in this project's own .venv; published Kokoro 0.9.4 excludes Python >=3.13")
    missing = [name for name, found in modules.items() if not found]
    if missing:
        blockers.append("Missing voice dependencies/assets: " + ", ".join(missing))
    if not espeak:
        blockers.append("espeak-ng command/library missing; install it for English fallback pronunciation")
    if not audio_only and (not ffmpeg or not ffprobe):
        blockers.append("FFmpeg and ffprobe are required to probe and mux the final video")
    return {
        "engine": "kokoro", "capabilities": {"reference_audio": False, "reference_text_required": False, "cloning": False, "speed_control": True},
        "python": sys.version.split()[0], "python_supported": supported,
        "isolated_environment": sys.prefix != sys.base_prefix, "modules": modules,
        "ffmpeg": ffmpeg, "ffprobe": ffprobe, "espeak_ng": espeak,
        "model_preflight": "not_checked", "model_installed": "not_established_by_dependency_detection",
        "cache": str(config.cache), "prerequisites_ready": not blockers,
        "ready": False, "readiness_basis": "model_not_checked", "blockers": blockers,
    }


def verify_plan_unchanged(plan: Plan) -> None:
    if sha256(plan.video) != plan.digest:
        raise StudioError("Video changed after planning; rerun against the current edit")
    for filename, digest in plan.inputs.items():
        if sha256(Path(filename)) != digest:
            raise StudioError(f"Input changed after planning: {filename}; regenerate the plan")


def probe_video(plan: Plan, config: Config) -> dict:
    command = executable(config.ffprobe)
    if not command:
        raise StudioError("ffprobe not found; install FFmpeg or use --audio-only (media metadata then remains unprobed)")
    result = subprocess.run([command, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", str(plan.video)], capture_output=True, text=True)
    if result.returncode:
        raise StudioError(f"ffprobe could not read video: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout); stream = data["streams"][0]
        duration = float(stream.get("duration", data.get("format", {}).get("duration")))
        fps_parts = stream["r_frame_rate"].split("/")
        fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
    except (KeyError, ValueError, IndexError, TypeError, ZeroDivisionError) as exc:
        raise StudioError("ffprobe returned incomplete/invalid video metadata") from exc
    duration = number(duration, "actual video duration", 0, True)
    fps = number(fps, "actual video fps", 0, True)
    if stream["width"] != plan.width or stream["height"] != plan.height or abs(duration-plan.duration) > max(.1, 2/plan.fps) or abs(fps-plan.fps) > .05:
        raise StudioError("Actual video dimensions/duration/fps differ from its narration sidecar")
    return {"duration": duration, "width": stream["width"], "height": stream["height"], "fps": fps}


def pronounced(text: str, replacements: dict[str, str]) -> str:
    # Literal whole-word substitutions only; arbitrary regex and code are not executed.
    for original in sorted(replacements, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(original) + r"(?!\w)", lambda _: replacements[original], text)
    return text


def validated_samples(samples, rate: int) -> array:
    try:
        values = array("f", samples)
    except (TypeError, ValueError, OverflowError) as exc:
        raise StudioError("Synthesizer returned invalid mono samples") from exc
    if len(values) < int(.05*rate) or not all(math.isfinite(x) for x in values):
        raise StudioError("Synthesizer returned empty, too-short, or non-finite audio")
    if max(abs(x) for x in values) < 1e-6:
        raise StudioError("Synthesizer returned silent audio; successful exit is not usable speech")
    return values


def fit_clip(clip: Clip, config: Config, synthesize: Callable[[str, float], array]) -> tuple[array, dict]:
    text = pronounced(clip.text, config.pronunciation)
    available = clip.end-clip.start-config.end_padding_seconds
    if available <= 0:
        raise StudioError(f"{clip.id}: no speech time remains after end padding")
    speed = config.speed
    attempts = []
    for attempt in range(config.max_attempts):
        samples = validated_samples(synthesize(text, speed), config.sample_rate)
        seconds = len(samples)/config.sample_rate
        attempts.append({"speed": round(speed, 5), "seconds": seconds})
        if len(samples) <= int(available*config.sample_rate):
            return samples, {**asdict(clip), "synthesis_text": text, "speech_seconds": seconds, "speed": speed, "attempts": attempts}
        if speed >= config.max_speed-1e-8 or attempt+1 == config.max_attempts:
            break
        # Re-synthesize at a bounded faster speaking rate, never trim words or time-stretch.
        speed = min(config.max_speed, max(speed+.02, speed*seconds/available*1.015))
    raise TimingOverflow({**asdict(clip), "available_seconds": available, "observed_seconds": seconds,
                          "minimum_slot_seconds_at_last_attempt": seconds+config.end_padding_seconds,
                          "max_speed": config.max_speed, "last_speed": speed, "attempts": attempts,
                          "action": "retime_video_and_captions_or_edit_narration; never_truncate"})


def assemble_track(plan: Plan, config: Config, synthesize: Callable[[str, float], array]) -> tuple[array, list[tuple[Clip, array, dict]], float]:
    track = array("f", [0.0]) * round(plan.duration*config.sample_rate)
    rendered = []
    for clip in plan.clips:
        samples, receipt = fit_clip(clip, config, synthesize)
        offset = round(clip.start*config.sample_rate)
        if offset+len(samples) > len(track):
            raise StudioError(f"{clip.id}: generated audio would run beyond video duration")
        track[offset:offset+len(samples)] = samples
        rendered.append((clip, samples, receipt))
    peak = max(abs(x) for x in track)
    gain = min(1.0, config.peak_limit/peak) if peak else 1.0
    if gain < 1:
        track = array("f", (x*gain for x in track))
    return track, rendered, gain


def write_wav(path: Path, samples: array, rate: int, peak_limit=.95) -> None:
    samples = validated_samples(samples, rate)
    peak = max(abs(x) for x in samples)
    gain = min(1.0, peak_limit/peak)
    pcm = array("h", (round(max(-1.0, min(1.0, x*gain))*32767) for x in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    with wave.open(str(path), "rb") as check:
        if check.getnframes() != len(samples) or check.getframerate() != rate:
            raise StudioError("WAV verification failed")


def loudness_filter(config: Config, report=False) -> str | None:
    if config.loudness_lufs is None:
        return None
    value=f"loudnorm=I={config.loudness_lufs:g}:TP={config.true_peak_db:g}:LRA={config.loudness_range:g}"
    return value+":print_format=json" if report else value


def loudness_targets(config: Config) -> dict | None:
    if config.loudness_lufs is None:
        return None
    return {"integrated_lufs":config.loudness_lufs,"true_peak_dbfs":config.true_peak_db,"loudness_range_lu":config.loudness_range,
            "basis":"editorial playback target; not a YouTube requirement"}


def measure_loudness(path: Path, config: Config) -> dict:
    ffmpeg=executable(config.ffmpeg)
    if not ffmpeg:
        raise StudioError("ffmpeg is required for loudness measurement")
    filter_value=loudness_filter(config,report=True) or "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json"
    result=subprocess.run([ffmpeg,"-hide_banner","-nostats","-i",str(path),"-vn","-af",filter_value,"-f","null","-"],capture_output=True,text=True)
    if result.returncode:
        raise StudioError(f"Loudness measurement failed: {result.stderr[-1200:]}")
    matches=re.findall(r'\{\s*"input_i"[\s\S]*?\}',result.stderr)
    if not matches:
        raise StudioError("FFmpeg did not return a loudnorm measurement report")
    try:
        data=json.loads(matches[-1])
        values={"integrated_lufs":float(data["input_i"]),"true_peak_dbfs":float(data["input_tp"]),"loudness_range_lu":float(data["input_lra"])}
    except (ValueError,KeyError,TypeError) as exc:
        raise StudioError("FFmpeg returned an invalid loudness measurement") from exc
    if not all(math.isfinite(value) for value in values.values()):
        raise StudioError("Loudness measurement is non-finite; check for silent/invalid audio")
    return {**values,"method":"FFmpeg loudnorm analysis; input_* fields describe the measured input","analysis_report":data}


def video_stream_hash(path: Path, config: Config) -> str:
    ffmpeg=executable(config.ffmpeg)
    if not ffmpeg:
        raise StudioError("ffmpeg is required to verify the copied video stream")
    result=subprocess.run([ffmpeg,"-v","error","-i",str(path),"-map","0:v:0","-c:v","copy","-an","-f","hash","-hash","sha256","-"],capture_output=True,text=True)
    match=re.fullmatch(r"SHA256=([0-9a-f]{64})\s*",result.stdout)
    if result.returncode or not match:
        raise StudioError(f"Cannot verify copied video stream: {result.stderr.strip()}")
    return match.group(1)


def mux_arguments(plan: Plan, config: Config, audio: Path, destination: Path, copy_video=False) -> list[str]:
    ffmpeg = executable(config.ffmpeg)
    if not ffmpeg:
        raise StudioError("ffmpeg not found; narration.wav is available, final video was not muxed")
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-n", "-i", str(plan.video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    if config.video_mode == "h264" and not copy_video:
        args += ["-c:v", "libx264", "-crf", str(config.crf), "-preset", "medium", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]
    audio_filter=loudness_filter(config)
    if audio_filter:
        args += ["-af",audio_filter]
    args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", str(plan.duration), "-movflags", "+faststart", str(destination)]
    return args


def mux_video(plan: Plan, config: Config, audio: Path, destination: Path, copy_video=False) -> list[str]:
    args=mux_arguments(plan,config,audio,destination,copy_video=copy_video)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise StudioError(f"FFmpeg mux failed; narration.wav retained: {result.stderr.strip()}")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise StudioError("FFmpeg returned without a nonempty video file")
    return args

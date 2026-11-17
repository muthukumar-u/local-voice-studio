"""Explicit provider adapters; all return finite mono PCM at Config.sample_rate.

Importing this module performs no model imports, network requests, or synthesis.
"""
from __future__ import annotations

from array import array
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave

from .core import Config, StudioError, executable, number, required_text, sha256, validated_samples

MAX_AUDIO_BYTES = 32 * 1024 * 1024
CAPABILITIES = {
    "kokoro": {"reference_audio": False, "reference_text_required": False, "cloning": False, "speed_control": True},
    "openai_compatible": {"reference_audio": False, "reference_text_required": False, "cloning": False, "speed_control": True},
    "mlx_audio": {"reference_audio": True, "reference_text_required": True, "cloning": True, "speed_control": False},
}


def validate_provider_config(config: Config) -> None:
    if config.engine not in CAPABILITIES:
        raise StudioError("engine must be kokoro, openai_compatible, or mlx_audio")
    if not isinstance(config.provider_python, str):
        raise StudioError("provider.python must be a Python executable path string")
    config.provider_timeout_seconds = number(config.provider_timeout_seconds, "provider.timeout_seconds", 0, True)
    if config.provider_timeout_seconds > 600:
        raise StudioError("provider.timeout_seconds must be <=600")
    if not isinstance(config.provider_api_key_env, str) or (config.provider_api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.provider_api_key_env)):
        raise StudioError("provider.api_key_env must name an environment variable, never contain its secret value")
    if config.engine != "mlx_audio" and (config.reference_audio is not None or config.reference_text is not None):
        raise StudioError(f"{config.engine} does not support reference audio or cloning")
    if config.engine == "kokoro":
        if config.model != "hexgrad/Kokoro-82M":
            raise StudioError("Kokoro requires model='hexgrad/Kokoro-82M'")
        if config.language not in ("a", "b"):
            raise StudioError("Kokoro supports language='a' (US English) or 'b' (UK English)")
        if not re.fullmatch(r"[ab][fm]_[a-z]+", config.voice) or not config.voice.startswith(config.language):
            raise StudioError("Kokoro voice must match its English language, e.g. af_heart with language='a'")
        if config.sample_rate != 24000:
            raise StudioError("Kokoro output is 24000 Hz; sample_rate must be 24000")
    elif config.engine == "openai_compatible":
        endpoint(config)
        required_text(config.model, "model")
        required_text(config.voice, "voice")
    else:
        if "Qwen3-TTS" not in config.model or not re.search(r"-Base(?:-|$)", config.model):
            raise StudioError("mlx_audio currently supports Qwen3-TTS Base models only; CustomVoice and VoiceDesign are different APIs")
        if config.device != "mps":
            raise StudioError("This optional MLX adapter requires device='mps' on Apple Silicon")
        if config.voice:
            raise StudioError("Qwen3-TTS Base cloning does not accept a preset voice; omit voice or set it to an empty string")
        if config.speed != 1.0 or config.max_speed != 1.0:
            raise StudioError("MLX Base has no implemented speed control: set speed=max_speed=1.0 and retime the edit on overflow")
        if config.language != "auto":
            raise StudioError("This MLX Base adapter uses automatic language selection; set language='auto'")


def endpoint(config: Config) -> str:
    value = required_text(config.provider_base_url, "provider.base_url")
    parsed = urllib.parse.urlsplit(value)
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise StudioError("provider.base_url must not contain credentials, query parameters, or fragments")
    try:
        parsed.port
    except ValueError as exc:
        raise StudioError("provider.base_url has an invalid port") from exc
    if any(ord(char) < 32 for char in value):
        raise StudioError("provider.base_url must not contain control characters")
    loopback = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if not parsed.hostname or parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not loopback):
        raise StudioError("provider.base_url requires HTTPS, or HTTP on an explicit loopback host")
    root = value.rstrip("/")
    return root if root.endswith("/audio/speech") else root + "/audio/speech"


def references(config: Config, audio=None, text=None) -> tuple[Path | None, str | None]:
    selected_audio = config.reference_audio if audio is None else audio
    selected_text = config.reference_text if text is None else text
    if selected_audio is not None or selected_text is not None:
        if not CAPABILITIES[config.engine]["reference_audio"]:
            raise StudioError(f"{config.engine} does not support reference audio or cloning; no synthesis or upload attempted")
    if config.engine != "mlx_audio":
        return None, None
    if selected_audio is None or selected_text is None:
        raise StudioError("MLX Qwen3-TTS Base requires both reference audio and its exact transcript")
    audio_path = Path(selected_audio).expanduser().resolve()
    if not audio_path.is_file() or audio_path.stat().st_size > MAX_AUDIO_BYTES:
        raise StudioError("Reference audio must be an existing WAV smaller than 32 MiB")
    try:
        with wave.open(str(audio_path), "rb") as stream:
            if stream.getnframes() <= 0 or stream.getframerate() < 8000 or stream.getnchannels() not in (1, 2):
                raise StudioError("Reference WAV needs nonempty mono/stereo audio at >=8000 Hz")
            pcm = stream.readframes(stream.getnframes())
            if not pcm or len(pcm) != stream.getnframes()*stream.getnchannels()*stream.getsampwidth():
                raise StudioError("Reference WAV is truncated")
            if (stream.getsampwidth() == 1 and all(value == 128 for value in pcm)) or (stream.getsampwidth() != 1 and not any(pcm)):
                raise StudioError("Reference WAV is silent; provide an actual speaker recording")
    except (OSError, wave.Error, EOFError) as exc:
        raise StudioError("Reference audio must be a readable PCM WAV") from exc
    return audio_path, required_text(selected_text, "reference.text")


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def backend_status(config: Config, audio_only=False, reference_audio=None, reference_text=None, check_reference=True) -> dict:
    """Dependency/config inspection only. `ready` never implies untested synthesis."""
    reference_supplied = any(item is not None for item in (reference_audio, reference_text, config.reference_audio, config.reference_text))
    if config.engine == "kokoro":
        from .core import environment_status
        status = environment_status(config, audio_only=audio_only)
        status["capabilities"] = dict(CAPABILITIES["kokoro"])
        try:
            if check_reference and reference_supplied:
                references(config, reference_audio, reference_text)
        except StudioError as exc:
            status["blockers"].append(str(exc))
            status["prerequisites_ready"] = False
        return status
    blockers = []
    try:
        validate_provider_config(config)
        if check_reference and reference_supplied:
            references(config, reference_audio, reference_text)
    except StudioError as exc:
        blockers.append(str(exc))
    modules = {}
    if config.engine == "openai_compatible":
        if config.provider_api_key_env and not os.environ.get(config.provider_api_key_env):
            blockers.append(f"Missing credentials in environment variable {config.provider_api_key_env}")
    elif config.engine == "mlx_audio":
        names = ("mlx_audio", "mlx", "numpy", "soundfile", "huggingface_hub")
        runtime_platform, runtime_machine = sys.platform, platform.machine()
        if config.provider_python:
            worker = Path(config.provider_python).expanduser()
            if not worker.is_file() or not os.access(worker, os.X_OK):
                blockers.append("Configured provider.python is missing or not executable; create the separate MLX environment explicitly")
            else:
                try:
                    probe = subprocess.run([str(worker), "-m", "local_voice_studio.runtime_worker", "--status"],
                                           cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
                    data = json.loads(probe.stdout)
                    if probe.returncode or not isinstance(data, dict) or not isinstance(data.get("modules"), dict):
                        raise ValueError("invalid worker status")
                    modules = {name: data["modules"].get(name) is True for name in names}
                    runtime_platform = required_text(data.get("platform"), "worker.platform")
                    runtime_machine = required_text(data.get("machine"), "worker.machine")
                except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, StudioError):
                    blockers.append("Configured provider.python could not return valid runtime_worker status within 10 seconds")
            if not modules:
                modules = {name: False for name in names}
        else:
            modules = {name: _has_module(name) for name in names}
        if runtime_platform != "darwin" or runtime_machine != "arm64":
            blockers.append("MLX Audio requires an Apple Silicon macOS runtime")
        if not all(modules.values()):
            blockers.append("MLX Audio is optional and not installed in the selected runtime; configure a separate compatible MLX environment, not the locked Kokoro environment")
    ffmpeg, ffprobe = executable(config.ffmpeg), executable(config.ffprobe)
    if not audio_only and (not ffmpeg or not ffprobe):
        blockers.append("FFmpeg and ffprobe are required to probe and mux video")
    return {"engine": config.engine, "capabilities": dict(CAPABILITIES.get(config.engine, {})),
            "python": sys.version.split()[0], "python_supported": sys.version_info[:2] >= (3, 11),
            "isolated_environment": sys.prefix != sys.base_prefix, "modules": modules,
            "ffmpeg": ffmpeg, "ffprobe": ffprobe, "espeak_ng": None,
            "cache": str(config.cache), "prerequisites_ready": not blockers,
            "model_preflight": "not_checked", "model_installed": "not_established_by_dependency_detection",
            "provider_python": config.provider_python or None,
            "runtime_platform": runtime_platform if config.engine == "mlx_audio" else sys.platform,
            "runtime_machine": runtime_machine if config.engine == "mlx_audio" else platform.machine(),
            "cloning_verified": False, "ready": False, "readiness_basis": "synthesis_not_checked", "blockers": blockers}


def pcm_wav(data: bytes, config: Config) -> array:
    """Decode bounded PCM WAV and normalize channel count/rate without model imports."""
    if len(data) > MAX_AUDIO_BYTES or len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise StudioError("Provider must return a bounded RIFF/WAVE audio response")
    try:
        with wave.open(io.BytesIO(data), "rb") as stream:
            channels, width, rate, frames = stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()
            if channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000 or frames <= 0:
                raise StudioError("Provider WAV needs nonempty mono/stereo PCM at a supported sample rate")
            raw = stream.readframes(frames)
            if len(raw) != frames*channels*width:
                raise StudioError("Provider returned truncated WAV audio")
    except (OSError, wave.Error, EOFError) as exc:
        raise StudioError("Provider returned malformed or unsupported PCM WAV") from exc
    if frames*config.sample_rate/rate*4 > MAX_AUDIO_BYTES:
        raise StudioError("Normalized provider audio exceeds 32 MiB; split narration into smaller passages")
    if rate != config.sample_rate:
        command = executable(config.ffmpeg)
        if not command:
            raise StudioError("Provider WAV sample rate differs; configure FFmpeg for normalized PCM output")
        try:
            result = subprocess.run([command, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-ar", str(config.sample_rate), "-f", "f32le", "pipe:1"], input=data, capture_output=True, timeout=config.provider_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StudioError("Provider WAV conversion failed or timed out") from exc
        if result.returncode or len(result.stdout) % 4:
            raise StudioError("FFmpeg could not normalize the provider WAV")
        samples = array("f"); samples.frombytes(result.stdout)
        if sys.byteorder != "little":
            samples.byteswap()
    else:
        samples = array("f")
        stride = channels * width
        for offset in range(0, len(raw), stride):
            total = 0.0
            for channel in range(channels):
                item = raw[offset + channel*width:offset + (channel+1)*width]
                value = (item[0]-128)/128 if width == 1 else int.from_bytes(item, "little", signed=True)/(2**(width*8-1))
                total += value
            samples.append(total/channels)
    return validated_samples(samples, config.sample_rate)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise StudioError("Speech endpoint redirected; configure its final URL explicitly")


class OpenAICompatibleBackend:
    capabilities = dict(CAPABILITIES["openai_compatible"])

    def __init__(self, config: Config):
        self.config = config
        self.endpoint = endpoint(config)
        if config.provider_api_key_env and not os.environ.get(config.provider_api_key_env):
            raise StudioError(f"Missing credentials in environment variable {config.provider_api_key_env}")
        self.provenance = {"engine": config.engine, "model": config.model, "voice": config.voice,
                           "endpoint": self.endpoint, "audio_transport": "WAV", "server_model_revision": "not_reported",
                           "cloning": False, "word_coverage": "requested text; no independent ASR verification"}

    def synthesize(self, text: str, speed: float) -> array:
        text = required_text(text, "narration")
        speed = number(speed, "speed", .25)
        if speed > 4:
            raise StudioError("Compatible speech speed must be <=4")
        payload = json.dumps({"model": self.config.model, "voice": self.config.voice, "input": text,
                              "speed": speed, "response_format": "wav"}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
        if self.config.provider_api_key_env:
            headers["Authorization"] = "Bearer " + os.environ[self.config.provider_api_key_env]
        request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.config.provider_timeout_seconds) as response:
                data = response.read(MAX_AUDIO_BYTES+1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise StudioError(f"Remote speech request failed with HTTP {code}; server body omitted") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise StudioError("Remote speech endpoint could not complete the request; check endpoint/network/timeout") from None
        return pcm_wav(data, self.config)


class MLXAudioBackend:
    capabilities = dict(CAPABILITIES["mlx_audio"])

    def __init__(self, config: Config, reference_audio: Path, reference_text: str, allow_downloads=False):
        status = backend_status(config, audio_only=True, reference_audio=reference_audio, reference_text=reference_text)
        if status["blockers"]:
            raise StudioError("; ".join(status["blockers"]))
        self.config, self.reference_audio, self.reference_text = config, reference_audio, reference_text
        self.reference_digest = sha256(reference_audio)
        try:
            from .backend import model_environment
            with model_environment(config, allow_downloads), redirect_stdout(sys.stderr):
                from huggingface_hub import snapshot_download
                model_path = Path(config.model).expanduser()
                if model_path.is_dir():
                    model_path = model_path.resolve()
                else:
                    model_path = Path(snapshot_download(repo_id=config.model, cache_dir=str(config.cache/"huggingface/hub"), local_files_only=not allow_downloads))
                import mlx.core as mx
                mx.set_default_device(mx.gpu)
                from mlx_audio.tts.utils import load_model
                self.model = load_model(str(model_path))
        except Exception as exc:
            raise StudioError("MLX model could not load from the selected local cache; install its isolated environment and prepare the selected Base model explicitly") from exc
        self.provenance = {"engine": config.engine, "model": config.model, "resolved_model": str(model_path),
                           "reference_audio_sha256": self.reference_digest, "reference_text": reference_text,
                           "cloning": "reference_conditioned; not listening_verified", "speed_control": False,
                           "word_coverage": "requested text; no independent ASR verification"}

    def synthesize(self, text: str, speed: float) -> array:
        if speed != 1.0:
            raise StudioError("MLX Base adapter cannot change synthesis speed; retime instead")
        if sha256(self.reference_audio) != self.reference_digest:
            raise StudioError("Reference audio changed after backend initialization")
        try:
            import numpy as np
            from .backend import model_environment
            chunks = []
            with model_environment(self.config, False), redirect_stdout(sys.stderr):
                results = self.model.generate(text=required_text(text,"narration"), ref_audio=str(self.reference_audio), ref_text=self.reference_text)
                for result in results:
                    rate = getattr(result, "sample_rate", getattr(self.model,"sample_rate",None))
                    if not isinstance(rate, (int, np.integer)) or rate <= 0:
                        raise StudioError("MLX result did not identify its audio sample rate")
                    audio = np.asarray(result.audio, dtype=np.float32)
                    if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                        raise StudioError("MLX returned empty/nonfinite/non-mono audio")
                    if rate == self.config.sample_rate:
                        chunks.append(validated_samples(audio.tolist(), self.config.sample_rate))
                        continue
                    peak = float(np.max(np.abs(audio)))
                    if peak > 1.0:
                        audio = audio * (self.config.peak_limit / peak)
                    # Convert other-rate model results to a standard WAV for normalization.
                    import soundfile as sf
                    buffer = io.BytesIO()
                    sf.write(buffer, audio, rate, format="WAV", subtype="PCM_16")
                    chunks.append(pcm_wav(buffer.getvalue(), self.config))
            samples = array("f")
            for chunk in chunks:
                samples.extend(chunk)
            return validated_samples(samples, self.config.sample_rate)
        except StudioError:
            raise
        except Exception as exc:
            raise StudioError("MLX reference-conditioned synthesis failed; inspect the selected model/environment") from exc


def create_backend(config: Config, allow_downloads=False, reference_audio=None, reference_text=None):
    """Factory shared by v1 commands and generic media products; no silent fallback."""
    validate_provider_config(config)
    audio, text = references(config, reference_audio, reference_text)
    if config.engine == "kokoro":
        from .backend import KokoroBackend
        backend = KokoroBackend(config, allow_downloads=allow_downloads)
        backend.capabilities = dict(CAPABILITIES["kokoro"])
        return backend
    if config.engine == "openai_compatible":
        return OpenAICompatibleBackend(config)
    return MLXAudioBackend(config, audio, text, allow_downloads=allow_downloads)

"""Kokoro adapter: imports/downloads happen only for actual synthesis or preflight."""
from array import array
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys

from .core import Config, StudioError, environment_status, sha256, validated_samples, word_sequence
from .model_assets import ensure_pinned_assets


@contextmanager
def model_environment(config: Config, allow_downloads: bool):
    keys = {"HF_HOME": str(config.cache / "huggingface"),
            "HF_HUB_CACHE": str(config.cache / "huggingface" / "hub"),
            "HUGGINGFACE_HUB_CACHE": str(config.cache / "huggingface" / "hub"),
            "TORCH_HOME": str(config.cache / "torch"),
            "HF_HUB_OFFLINE": "0" if allow_downloads else "1", "TRANSFORMERS_OFFLINE": "0" if allow_downloads else "1",
            "HF_DATASETS_OFFLINE": "0" if allow_downloads else "1"}
    if config.device == "mps":
        keys["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class KokoroBackend:
    def __init__(self, config: Config, allow_downloads=False):
        self.config = config
        if not (3, 11) <= sys.version_info[:2] < (3, 13):
            raise StudioError("Kokoro 0.9.4 needs this tool's Python 3.11/3.12 .venv; current Python is unsupported")
        try:
            for name in ("kokoro", "numpy", "soundfile", "torch", "spacy"):
                if importlib.util.find_spec(name) is None:
                    raise StudioError(f"Missing {name}; run setup.sh with Python 3.12 in this separate project")
        except (ImportError, ValueError) as exc:
            raise StudioError(f"Dependency inspection failed: {exc}") from exc
        if importlib.util.find_spec("en_core_web_sm") is None:
            # Misaki otherwise attempts a spaCy package download even in HF offline mode.
            raise StudioError("Missing en_core_web_sm. In the project .venv run: python -m spacy download en_core_web_sm. No automatic install was attempted.")
        if not environment_status(config, audio_only=True)["espeak_ng"]:
            raise StudioError("espeak-ng command/library missing; install the English fallback before synthesis so unfamiliar words are not silently skipped")
        if allow_downloads and sys.prefix == sys.base_prefix:
            raise StudioError("Model downloads must run from the project's isolated .venv, not a global interpreter")
        self.provenance = ensure_pinned_assets(config, allow_downloads=allow_downloads)
        try:
            with model_environment(config, False):
                import numpy as np
                if importlib.util.find_spec("espeakng_loader") is not None:
                    import espeakng_loader
                    from phonemizer.backend.espeak.wrapper import EspeakWrapper
                    EspeakWrapper.set_library(espeakng_loader.get_library_path())
                    if hasattr(EspeakWrapper, "set_data_path"):
                        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
                from kokoro import KPipeline
                self.np = np
                self.pipeline = KPipeline(lang_code=config.language, repo_id=config.model, device=config.device)
                self.pipeline.load_voice(config.voice)
        except Exception as exc:
            mode = "Download-enabled" if allow_downloads else "Cache-only (no-download)"
            raise StudioError(f"{mode} Kokoro initialization failed: {exc}. Weights/voice/G2P assets are not ready; no speech was generated.") from exc
        self.allow_downloads = False  # Even download-enabled setup may infer only from the verified pinned snapshot.

    def synthesize(self, text: str, speed: float) -> array:
        chunks = []
        returned_text = []
        try:
            with model_environment(self.config, self.allow_downloads):
                for graphemes, phonemes, audio in self.pipeline(text, voice=self.config.voice, speed=speed, split_pattern=r"\n+"):
                    if audio is None:
                        raise StudioError("Kokoro yielded phonemes without audio")
                    values = audio.detach().cpu().numpy() if hasattr(audio, "detach") else self.np.asarray(audio)
                    if values.ndim != 1:
                        raise StudioError("Kokoro returned non-mono audio")
                    chunks.append(validated_samples(values.tolist(), self.config.sample_rate))
                    returned_text.append(graphemes)
        except StudioError:
            raise
        except Exception as exc:
            raise StudioError(f"Kokoro synthesis failed: {exc}") from exc
        if not chunks:
            raise StudioError("Kokoro returned no audio chunks")
        if word_sequence(" ".join(returned_text)) != word_sequence(text):
            raise StudioError("Kokoro's returned text does not cover all requested words; refusing a potentially truncated narration")
        result = array("f")
        gap = array("f", [0.0])*round(self.config.chunk_pause_seconds*self.config.sample_rate)
        for index, chunk in enumerate(chunks):
            if index:
                result.extend(gap)
            result.extend(chunk)
        return validated_samples(result, self.config.sample_rate)

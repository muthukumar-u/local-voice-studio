"""Provider contracts: local mock HTTP and waveforms, never paid speech or cloned voices."""
from array import array
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

from local_voice_studio.core import Config, StudioError, environment_status, load_config
from local_voice_studio.providers import backend_status, create_backend, pcm_wav


def wav_bytes(rate=24000, channels=1, silent=False):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(b"".join(struct.pack("<h", 0 if silent else round(math.sin(i*2*math.pi*240/rate)*9000)) * channels for i in range(rate//10)))
    return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    payload = wav_bytes()
    status = 200
    calls = []

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append({"path": self.path, "body": data, "authorization": self.headers.get("Authorization")})
        self.send_response(self.status)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="voice-provider-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def remote(self, base_url="http://127.0.0.1:8000/v1", **values):
        return replace(Config(), engine="openai_compatible", model="test-model", voice="test-voice", provider_base_url=base_url, **values)

    def config_file(self, text):
        path = self.root/"profile.toml"; path.write_text(text)
        return path

    def test_default_kokoro_configuration_is_unchanged(self):
        config = load_config(None)
        self.assertEqual((config.engine,config.model,config.voice,config.language,config.sample_rate),("kokoro","hexgrad/Kokoro-82M","af_heart","a",24000))
        self.assertFalse(backend_status(config,audio_only=True)["capabilities"]["cloning"])

    def test_provider_specific_configuration_and_relative_reference(self):
        config = load_config(self.config_file('[voice]\nengine="openai_compatible"\nmodel="server-model"\nvoice="speaker"\nlanguage="en"\nsample_rate=22050\n[provider]\nbase_url="https://speech.example/v1"\napi_key_env="MY_VOICE_SECRET"\ntimeout_seconds=10\n'))
        self.assertEqual(config.sample_rate,22050)
        self.assertEqual(config.provider_api_key_env,"MY_VOICE_SECRET")
        mlx = load_config(self.config_file('[voice]\nengine="mlx_audio"\nmodel="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"\n[reference]\naudio="sample.wav"\ntext="The recording transcript."\n'))
        self.assertEqual(mlx.reference_audio,(self.root/"sample.wav").resolve())
        self.assertEqual((mlx.voice,mlx.language,mlx.device,mlx.speed,mlx.max_speed),("","auto","mps",1.0,1.0))

    def test_unknown_provider_options_and_insecure_endpoint_rejected(self):
        for text in ['[provider]\napi_key="not-a-secret-setting"\n', '[voice]\nengine="openai_compatible"\n[provider]\nbase_url="http://remote.example/v1"\n', '[voice]\nengine="openai_compatible"\n[provider]\nbase_url="https://user:password@remote.example/v1"\n']:
            with self.assertRaises(StudioError):
                load_config(self.config_file(text))

    def test_unsupported_reference_rejected_before_model_or_http(self):
        for config in (Config(),self.remote()):
            with patch("urllib.request.build_opener") as network:
                with self.assertRaisesRegex(StudioError,"does not support reference"):
                    create_backend(config,reference_audio=self.root/"missing.wav",reference_text="The sample.")
                network.assert_not_called()

    def test_missing_remote_credentials_block_before_request(self):
        config=self.remote(provider_api_key_env="VOICE_TEST_MISSING_SECRET")
        with patch.dict(os.environ,{},clear=True):
            self.assertTrue(backend_status(config,audio_only=True)["blockers"])
            with self.assertRaisesRegex(StudioError,"Missing credentials"):
                create_backend(config)

    def test_status_does_not_claim_provider_or_clone_ready(self):
        status=environment_status(self.remote(),audio_only=True)
        self.assertFalse(status["ready"])
        self.assertFalse(status["capabilities"]["cloning"])
        self.assertEqual(status["model_preflight"],"not_checked")
        config=load_config(self.config_file('[voice]\nengine="mlx_audio"\nmodel="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"\n'))
        with patch("local_voice_studio.providers._has_module",return_value=False):
            status=backend_status(config,audio_only=True)
        self.assertTrue(status["capabilities"]["cloning"])
        self.assertFalse(status["cloning_verified"])
        self.assertFalse(status["ready"])
        self.assertTrue(any("separate compatible MLX" in line for line in status["blockers"]))

    def test_mlx_rejects_preset_voice_and_fictitious_speed(self):
        root='[voice]\nengine="mlx_audio"\nmodel="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"\n'
        for extra in ['voice="Vivian"\n','speed=0.95\n','language="English"\n']:
            with self.assertRaises(StudioError):
                load_config(self.config_file(root+extra))

    def test_wave_validation_downmixes_and_rejects_bad_audio(self):
        samples=pcm_wav(wav_bytes(channels=2),Config())
        self.assertEqual(len(samples),2400)
        self.assertGreater(max(samples),.2)
        for data in (b'not WAV',wav_bytes(silent=True),wav_bytes()[:-90]):
            with self.assertRaises(StudioError):
                pcm_wav(data,Config())

    def test_sample_rate_normalization_uses_configured_ffmpeg(self):
        converted=array('f',[.25,-.25])*1200
        if __import__('sys').byteorder != 'little': converted.byteswap()
        with patch("local_voice_studio.providers.executable",return_value="/test/ffmpeg"), patch("local_voice_studio.providers.subprocess.run") as process:
            process.return_value.returncode=0;process.return_value.stdout=converted.tobytes()
            samples=pcm_wav(wav_bytes(rate=48000),Config())
            self.assertEqual(len(samples),2400)
            self.assertIn("24000",process.call_args.args[0])
            self.assertEqual(process.call_args.kwargs["input"],wav_bytes(rate=48000))

    def test_mocked_http_transport_contract_and_response_validation(self):
        config=self.remote(provider_api_key_env="VOICE_MOCK_KEY")
        with patch.dict(os.environ,{"VOICE_MOCK_KEY":"private-test-value"}), patch("urllib.request.build_opener") as factory:
            factory.return_value.open.return_value=io.BytesIO(wav_bytes(channels=2))
            backend=create_backend(config)
            samples=backend.synthesize("Complete requested text.",.95)
            self.assertEqual(len(samples),2400)
            request=factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,"http://127.0.0.1:8000/v1/audio/speech")
            self.assertEqual(request.get_header("Authorization"),"Bearer private-test-value")
            body=json.loads(request.data)
            self.assertEqual(body,{"input":"Complete requested text.","model":"test-model","voice":"test-voice","speed":.95,"response_format":"wav"})
            self.assertNotIn("private-test-value",json.dumps(backend.provenance))
            factory.return_value.open.return_value=io.BytesIO(b"not a WAV response")
            with self.assertRaisesRegex(StudioError,"WAVE"):
                backend.synthesize("No usable audio.",1)

    def test_http_error_omits_server_body_and_credentials(self):
        import urllib.error
        with patch("urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect=urllib.error.HTTPError("https://server.invalid",401,"Unauthorized",{},io.BytesIO(b"private response text"))
            backend=create_backend(self.remote())
            with self.assertRaisesRegex(StudioError,"HTTP 401") as error:
                backend.synthesize("Example.",1)
            self.assertNotIn("private",str(error.exception))

    def test_actual_ffmpeg_normalizes_remote_rate(self):
        binary=Path(__file__).resolve().parents[1]/".tools/media/node_modules/@ffmpeg-installer/darwin-arm64/ffmpeg"
        if not binary.is_file():
            self.skipTest("Project-local FFmpeg is not installed")
        samples=pcm_wav(wav_bytes(rate=48000),replace(Config(),ffmpeg=str(binary)))
        self.assertEqual(len(samples),2400)
        self.assertGreater(max(samples),.2)

    def mlx_config(self, **values):
        return replace(Config(),engine="mlx_audio",model="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",voice="",language="auto",device="mps",speed=1.0,max_speed=1.0,**values)

    def test_isolated_worker_reports_its_dependencies_not_parent(self):
        python=self.root/"clone-python";python.write_text("fixture");python.chmod(0o700)
        config=self.mlx_config(provider_python=str(python))
        response={"platform":"darwin","machine":"arm64","modules":{name:True for name in ("mlx_audio","mlx","numpy","soundfile","huggingface_hub")}}
        with patch("local_voice_studio.providers._has_module",side_effect=AssertionError("parent dependencies must not be used")),patch("local_voice_studio.providers.subprocess.run") as child:
            child.return_value.returncode=0;child.return_value.stdout=json.dumps(response)
            status=backend_status(config,audio_only=True)
        self.assertTrue(status["prerequisites_ready"])
        self.assertFalse(status["ready"])
        self.assertFalse(status["cloning_verified"])
        self.assertTrue(status["capabilities"]["reference_text_required"])
        self.assertEqual(status["runtime_platform"],"darwin")
        self.assertEqual(child.call_args.args[0],[str(python),"-m","local_voice_studio.runtime_worker","--status"])
        self.assertEqual(child.call_args.kwargs["timeout"],10)

    def test_missing_nonexecutable_or_bad_worker_is_blocked(self):
        python=self.root/"clone-python"
        with patch("local_voice_studio.providers.subprocess.run") as child:
            status=backend_status(self.mlx_config(provider_python=str(python)),audio_only=True)
            self.assertTrue(any("missing or not executable" in line for line in status["blockers"]))
            child.assert_not_called()
            python.write_text("fixture");python.chmod(0o700)
            child.return_value.returncode=0;child.return_value.stdout="not JSON"
            status=backend_status(self.mlx_config(provider_python=str(python)),audio_only=True)
            self.assertTrue(any("valid runtime_worker status" in line for line in status["blockers"]))

    def test_reference_validation_can_wait_for_facade_normalization(self):
        compressed=self.root/"reference.mp3";compressed.write_bytes(b"compressed fixture, not actual audio")
        config=self.mlx_config(reference_audio=compressed,reference_text="The recording.")
        with patch("local_voice_studio.providers._has_module",return_value=True):
            status=backend_status(config,audio_only=True,check_reference=False)
            self.assertFalse(any("Reference audio" in line for line in status["blockers"]))
            status=backend_status(config,audio_only=True)
            self.assertTrue(any("Reference audio" in line for line in status["blockers"]))
        with self.assertRaisesRegex(StudioError,"requires both"):
            create_backend(self.mlx_config())

    def test_provider_python_keeps_virtualenv_symlink_path(self):
        runtime=self.root/"runtime";runtime.mkdir();target=runtime/"python-real";target.write_text("fixture")
        link=runtime/"python";link.symlink_to(target)
        config=load_config(self.config_file('[voice]\nengine="mlx_audio"\nmodel="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"\n[provider]\npython="runtime/python"\n'))
        self.assertEqual(config.provider_python,str(self.root.resolve()/"runtime/python"))
        self.assertNotEqual(config.provider_python,str(target.resolve()))

    def test_mock_http_endpoint_uses_request_contract_and_normalizes_wav(self):
        Handler.payload=wav_bytes(channels=2);Handler.status=200;Handler.calls=[]
        try:
            server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        except OSError as exc:
            self.skipTest(f"Loopback server unavailable in this runtime: {exc}")
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            config=self.remote(f"http://127.0.0.1:{server.server_port}/v1",provider_api_key_env="VOICE_LOCAL_TEST_KEY")
            with patch.dict(os.environ,{"VOICE_LOCAL_TEST_KEY":"local-test-value"}):
                backend=create_backend(config)
                samples=backend.synthesize("Only local mock speech.",.95)
                self.assertEqual(len(samples),2400)
                call=Handler.calls[-1]
                self.assertEqual(call["path"],"/v1/audio/speech")
                self.assertEqual(call["authorization"],"Bearer local-test-value")
                self.assertEqual(call["body"],{"input":"Only local mock speech.","model":"test-model","voice":"test-voice","speed":.95,"response_format":"wav"})
                Handler.payload=b'not audio'
                with self.assertRaisesRegex(StudioError,"WAVE"):
                    backend.synthesize("Invalid audio response.",1)
                Handler.status=401;Handler.payload=b'secret server diagnostics'
                with self.assertRaises(StudioError) as error:
                    backend.synthesize("A rejected request.",1)
                self.assertNotIn('secret server',str(error.exception))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

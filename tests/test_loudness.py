from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_voice_studio.core import Config,Plan,StudioError,load_config,loudness_filter,mux_arguments


class LoudnessTests(unittest.TestCase):
    def test_audio_finishing_is_optional_without_loudness_target(self):
        self.assertIsNone(loudness_filter(Config()))

    def test_unsafe_and_nonfinite_targets_are_rejected(self):
        for key,value in [('loudness_lufs','nan'),('loudness_lufs','-2'),('true_peak_db','0.5'),('true_peak_db','-inf'),('loudness_range','0')]:
            with tempfile.TemporaryDirectory(prefix='voice-loudness-test-') as folder:
                path=Path(folder)/'config.toml';path.write_text(f'[mux]\n{key}={value}\n')
                with self.assertRaises(StudioError,msg=(key,value)):
                    load_config(path)

    def test_finishing_uses_loudnorm_without_video_or_speech_time_filters(self):
        config=replace(Config(),loudness_lufs=-16,true_peak_db=-1.5,loudness_range=11)
        plan=Plan(Path('prior.mp4'),Path('receipt.json'),'0'*64,122,1080,1920,30,[],[],None,None,{},'cue',{})
        with patch('local_voice_studio.core.executable',return_value='/local/ffmpeg'):
            command=mux_arguments(plan,config,Path('raw.wav'),Path('delivery.mp4'),copy_video=True)
        self.assertEqual(command[command.index('-af')+1],'loudnorm=I=-16:TP=-1.5:LRA=11')
        self.assertEqual(command[command.index('-c:v')+1],'copy')
        self.assertEqual(command[command.index('-t')+1],'122')
        self.assertFalse(any('atempo' in value or 'setpts' in value for value in command))
        self.assertEqual(command[command.index('-map')+1],'0:v:0')
        self.assertIn('1:a:0',command)

    def test_normal_render_also_applies_the_configured_delivery_filter(self):
        config=replace(Config(),loudness_lufs=-16)
        plan=Plan(Path('silent.mov'),Path('sidecar.json'),'0'*64,122,1080,1920,30,[],[],None,None,{},'cue',{})
        with patch('local_voice_studio.core.executable',return_value='/local/ffmpeg'):
            command=mux_arguments(plan,config,Path('raw.wav'),Path('delivery.mp4'))
        self.assertEqual(command[command.index('-c:v')+1],'libx264')
        self.assertIn('loudnorm=I=-16:TP=-1.5:LRA=11',command)


if __name__=='__main__':unittest.main()

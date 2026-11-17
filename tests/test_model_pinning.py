from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_voice_studio.core import Config, StudioError
from local_voice_studio.model_assets import ensure_pinned_assets


class ModelPinningTests(unittest.TestCase):
    def fixture(self, root):
        commit='1'*40; config=replace(Config(),cache=root/'custom-cache')
        cache=config.cache/'huggingface/hub/models--hexgrad--Kokoro-82M'
        files=[]
        for name in ['config.json','kokoro-v1_0.pth','voices/af_heart.pt']:
            data=('fixture '+name).encode();path=cache/'snapshots'/commit/name
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            files.append({'filename':name,'sha256':hashlib.sha256(data).hexdigest(),'url':f'https://huggingface.co/{config.model}/resolve/{commit}/{name}'})
        manifest=root/'model-sources.json';manifest.write_text(json.dumps({'repository':config.model,'commit':commit,'files':files}))
        return config,cache,manifest,commit

    def test_download_enabled_path_cannot_follow_newer_main(self):
        with tempfile.TemporaryDirectory(prefix='voice-pin-test-') as folder:
            config,cache,manifest,commit=self.fixture(Path(folder))
            (cache/'refs').mkdir();(cache/'refs/main').write_text('2'*40)
            with patch('local_voice_studio.model_assets.subprocess.run') as network:
                result=ensure_pinned_assets(config,allow_downloads=True,manifest_path=manifest)
                network.assert_not_called()
            self.assertEqual(result['revision'],commit)
            self.assertEqual((cache/'refs/main').read_bytes(),commit.encode())

    def test_mismatched_cached_weights_block_inference_even_when_downloads_allowed(self):
        with tempfile.TemporaryDirectory(prefix='voice-pin-test-') as folder:
            config,cache,manifest,commit=self.fixture(Path(folder))
            (cache/'snapshots'/commit/'kokoro-v1_0.pth').write_bytes(b'tampered')
            with patch('local_voice_studio.model_assets.subprocess.run') as network:
                with self.assertRaisesRegex(StudioError,'SHA-256 mismatch'):
                    ensure_pinned_assets(config,allow_downloads=True,manifest_path=manifest)
                network.assert_not_called()


if __name__=='__main__':unittest.main()

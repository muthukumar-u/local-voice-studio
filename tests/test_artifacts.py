import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("voice_artifacts",Path(__file__).resolve().parents[1]/"scripts/fetch_artifacts.py")
artifacts=importlib.util.module_from_spec(spec);spec.loader.exec_module(artifacts)


class ArtifactTests(unittest.TestCase):
    def test_artifact_paths_cannot_escape_wheelhouse(self):
        with tempfile.TemporaryDirectory(prefix="voice-artifact-test-") as folder:
            root=Path(folder)
            (root/'dependency-sources.json').write_text(json.dumps({'packages':[{'filename':'../escaped.whl','url':'https://pypi.org/file.whl','sha256':'0'*64}]}))
            (root/'toolchain-sources.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'simple names'):
                artifacts.artifact_plan(root)

    def test_existing_tampered_artifact_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory(prefix="voice-artifact-test-") as folder:
            path=Path(folder)/'fixture.whl';path.write_bytes(b'changed bytes')
            request={'path':str(path),'url':'https://pypi.org/file.whl','sha256':hashlib.sha256(b'original bytes').hexdigest()}
            with patch.object(artifacts,'artifact_plan',return_value=[request]),patch.object(artifacts.subprocess,'run') as network:
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    artifacts.main([])
                network.assert_not_called()

    def test_existing_verified_artifact_is_reused_without_network(self):
        with tempfile.TemporaryDirectory(prefix="voice-artifact-test-") as folder:
            path=Path(folder)/'fixture.whl';path.write_bytes(b'verified bytes')
            request={'path':str(path),'url':'https://pypi.org/file.whl','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            with patch.object(artifacts,'artifact_plan',return_value=[request]),patch.object(artifacts.subprocess,'run') as network:
                self.assertEqual(artifacts.main([]),0)
                network.assert_not_called()


if __name__=='__main__':unittest.main()

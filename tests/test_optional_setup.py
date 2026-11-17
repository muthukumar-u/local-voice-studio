import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(name):
    path=Path(__file__).resolve().parents[1]/"scripts"/(name+".py")
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


class OptionalSetupTests(unittest.TestCase):
    def test_clone_setup_preserves_existing_unowned_environment(self):
        setup=module("bootstrap_clone")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/".clone-venv";runtime.mkdir();(runtime/"keep").write_text("original")
            with patch.object(setup,"ROOT",root),patch.object(setup.sys,"platform","darwin"),patch.object(setup.platform,"machine",return_value="arm64"),patch.object(setup.subprocess,"run") as run:
                with self.assertRaisesRegex(ValueError,"not a marked"):setup.main([])
                run.assert_not_called()
            self.assertEqual((runtime/"keep").read_text(),"original")

    def test_clone_setup_needs_explicit_recreation_after_relocation(self):
        setup=module("bootstrap_clone")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/".clone-venv";runtime.mkdir();(runtime/"keep").write_text("original")
            (root/".tools").mkdir();(root/".tools/clone-environment.json").write_text(json.dumps({"owner":"local-voice-studio-clone","project_root":"/old-location"}))
            with patch.object(setup,"ROOT",root),patch.object(setup.sys,"platform","darwin"),patch.object(setup.platform,"machine",return_value="arm64"),patch.dict(os.environ,{"VOICE_RECREATE_CLONE_ENV":"0"}),patch.object(setup.subprocess,"run") as run:
                with self.assertRaisesRegex(ValueError,"moved"):setup.main([])
                run.assert_not_called()
            self.assertEqual((runtime/"keep").read_text(),"original")

    def test_tampered_cached_service_dependency_is_never_installed_or_replaced(self):
        setup=module("bootstrap_service")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/".venv/bin").mkdir(parents=True);(root/".venv/bin/python").write_text("placeholder")
            (root/".tools").mkdir();(root/".tools/environment.json").write_text(json.dumps({"owner":"local-voice-studio","project_root":str(root)}))
            (root/".cache/service-wheels").mkdir(parents=True);wheel=root/".cache/service-wheels/example.whl";wheel.write_bytes(b"tampered")
            (root/"service-sources.json").write_text(json.dumps({"artifacts":[{"filename":"example.whl","url":"https://example.test/example.whl","sha256":"0"*64}]}))
            with patch.object(setup,"ROOT",root),patch.object(setup.subprocess,"run") as run:
                with self.assertRaisesRegex(ValueError,"hash mismatch"):setup.main(["--offline"])
                run.assert_not_called()
            self.assertEqual(wheel.read_bytes(),b"tampered")


if __name__=="__main__":unittest.main()

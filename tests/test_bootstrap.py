import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("voice_bootstrap",Path(__file__).resolve().parents[1]/"scripts/bootstrap.py")
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_existing_unmarked_environment_is_never_overwritten(self):
        with self.assertRaisesRegex(RuntimeError,"not marked"):
            bootstrap.check_relocation(None,Path('/private/tmp/new-root'),True,True)

    def test_relocated_environment_requires_explicit_recreation(self):
        marker={'owner':'local-voice-studio','project_root':'/private/tmp/old-root'}
        with self.assertRaisesRegex(RuntimeError,"Project moved"):
            bootstrap.check_relocation(marker,Path('/private/tmp/new-root'),True,False)
        self.assertTrue(bootstrap.check_relocation(marker,Path('/private/tmp/new-root'),True,True))

    def test_current_environment_has_no_recreation(self):
        root=Path('/private/tmp/current-root')
        marker={'owner':'local-voice-studio','project_root':str(root)}
        self.assertFalse(bootstrap.check_relocation(marker,root,True,False))

    def test_new_folder_does_not_need_existing_marker(self):
        self.assertFalse(bootstrap.check_relocation(None,Path('/private/tmp/new-root'),False,False))


if __name__=='__main__':
    unittest.main()

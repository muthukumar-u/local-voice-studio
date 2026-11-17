import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from local_voice_studio.core import StudioError, TimingOverflow, sha256
from local_voice_studio.runtime_worker import delegate


class RuntimeWorkerTests(unittest.TestCase):
    def test_child_reports_its_runtime_without_loading_models(self):
        result = subprocess.run([sys.executable,"-m","local_voice_studio.runtime_worker","--status"], capture_output=True,text=True,check=True)
        status = json.loads(result.stdout)
        self.assertIn("platform",status)
        self.assertIn("mlx_audio",status["modules"])
        self.assertTrue(all(type(value) is bool for value in status["modules"].values()))

    def test_matching_outputs_are_verified_and_runtime_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);outputs={}
            for name in ("video","narration","receipt"):
                path=root/name;path.write_text(name)
                outputs[name]={"path":str(path),"sha256":sha256(path)}
            payload={"status":"succeeded","outputs":outputs,"summary":{"audio_present":True,"voice_generated":True}}
            with patch("local_voice_studio.runtime_worker.subprocess.run") as run:
                run.return_value.returncode=0;run.return_value.stdout=json.dumps(payload)
                self.assertEqual(delegate("/selected/.venv/bin/python",{"output_dir":directory}),payload)
                self.assertEqual(run.call_args.args[0][0],"/selected/.venv/bin/python")
                outputs["video"]["sha256"]="0"*64
                run.return_value.stdout=json.dumps(payload)
                with self.assertRaisesRegex(StudioError,"artifact"):
                    delegate("python",{"output_dir":directory})

    def test_worker_timing_overflow_keeps_its_actionable_details(self):
        details={"id":"C1","available_seconds":1.,"observed_seconds":2.,"last_speed":1.,"max_speed":1.}
        with patch("local_voice_studio.runtime_worker.subprocess.run") as run:
            run.return_value.returncode=3
            run.return_value.stdout=json.dumps({"status":"needs_retime","retime_plan":details})
            with self.assertRaises(TimingOverflow) as found:
                delegate("python",{})
            self.assertEqual(found.exception.details,details)

    def test_invalid_result_and_timeout_do_not_claim_generated_content(self):
        for output in ("not json",json.dumps({"status":"planned"})):
            with patch("local_voice_studio.runtime_worker.subprocess.run") as run:
                run.return_value.returncode=0;run.return_value.stdout=output
                with self.assertRaises(StudioError):delegate("python",{})
        with patch("local_voice_studio.runtime_worker.subprocess.run",side_effect=subprocess.TimeoutExpired("python",1)):
            with self.assertRaisesRegex(StudioError,"timeout"):delegate("python",{})


if __name__ == "__main__":unittest.main()

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class ExtractionTests(unittest.TestCase):
    def test_four_model_fields_and_trusted_metadata(self):
        fields = {
            "rank_minimum": "Diamond",
            "rank_maximum": "Grandmaster",
            "Start_Time_timestamp": "2027-01-15T08:00:00Z",
            "End_Time_timestamp": None,
        }

        def run(args, **kwargs):
            self.assertIn("gpt-5.5", args)
            self.assertIn('model_reasoning_effort="medium"', args)
            schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
            self.assertEqual(set(schema["properties"]["scrims"]["items"]["properties"]), set(fields))
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps({"scrims": [fields]}), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0)

        with (
            patch.dict(main.os.environ, {"CODEX_MODEL": "gpt-5.5", "CODEX_REASONING_EFFORT": "medium"}),
            patch.object(main.shutil, "which", return_value="codex.exe"),
            patch.object(main.subprocess, "run", side_effect=run),
        ):
            result = main.extract([{"id": "123", "content": "GM to Dia <t:1800000000:F>"}])["scrims"]
        self.assertEqual(result[0], {**fields, "source_message_id": "123"})


if __name__ == "__main__":
    unittest.main()

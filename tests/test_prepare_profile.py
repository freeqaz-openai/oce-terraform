"""Exercise the CLI against synthetic site settings and a stand-in Terraform executable."""

from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare-profile"
SECRET = "synthetic-do-not-print"
CONTROL = {"oce-role": "control"}
STORAGE = "gatewayStorageClassName"
PLACEMENT = {
    "controlPlane": {"nodeSelector": CONTROL},
    "runtime": {"gatewayNodeSelector": CONTROL, "nodeSelector": {"oce-role": "agents"}, STORAGE: "csi"},
}
EMPTY_SITE = {"controlPlane": {}, "runtime": {}}
FAKE = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
caller = Path(__file__).resolve().parent
assert Path.cwd() == caller
assert sys.argv[1:] == [f"-chdir={caller / 'target with spaces'}", "output", "-json", "oce_placement"]
assert os.path.samestat(os.fstat(0), os.stat(os.devnull))
if os.environ["RACE"] == "1":
    Path("out/new").write_text("competing writer")
sys.stdout.write(os.environ["PLACEMENT"])
sys.stderr.write(os.environ["SECRET"])
sys.exit(int(os.environ["EXIT"]))
"""


class PrepareProfileTest(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name).resolve()
        (self.root / "target with spaces").mkdir()
        (self.root / "python3").symlink_to(sys.executable)
        (self.root / "terraform").write_text(FAKE)
        (self.root / "terraform").chmod(0o700)
        self.output = self.root / "out" / "new"
        self.output.parent.mkdir(mode=0o700)
        self.input = self.root / f"{SECRET}.json"

    def invoke(self, *, site=EMPTY_SITE, placement=PLACEMENT, refusal=None, exit_code=0, race=False):
        raw = site if isinstance(site, str) else json.dumps(site)
        self.input.write_text(raw)
        env = {"PATH": str(self.root), "SECRET": SECRET, "EXIT": str(exit_code), "RACE": str(int(race)),
               "PLACEMENT": placement if isinstance(placement, str) else json.dumps(placement)}
        result = subprocess.run(
            [SCRIPT, "--target-dir", "target with spaces", "--input", self.input.name, "--output", "out/new"],
            cwd=self.root, env=env, input=SECRET, text=True, capture_output=True, timeout=5,
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.input.read_text(), raw)
        self.assertFalse(list(self.output.parent.glob(".oce-*")), "left a staging file")
        if refusal is None:
            self.assertEqual((result.returncode, result.stderr), (0, ""))
        else:
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(refusal, result.stderr)

    def test_adds_all_fields_and_preserves_restrictions_and_precise_site_settings(self):
        zone = {"topology.kubernetes.io/zone": "west"}
        site = {"controlPlane": {"nodeSelector": {**CONTROL, **zone}},
                "runtime": {"gatewayNodeSelector": zone, "nodeSelector": {"fixture/allow": ""}},
                "unrelated": [SECRET, True, [443, "NUMBER"]]}
        number = "1234567890.12345678901234567890123456789"
        self.invoke(site=json.dumps(site).replace('"NUMBER"', number))
        self.assertEqual(json.loads(self.output.read_text(), parse_float=Decimal), {
            "controlPlane": site["controlPlane"],
            "runtime": {"gatewayNodeSelector": {**CONTROL, **zone}, STORAGE: "csi",
                        "nodeSelector": {"fixture/allow": "", "oce-role": "agents"}},
            "unrelated": [SECRET, True, [443, Decimal(number)]],
        })
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_fills_absent_selectors_with_matching_storage(self):
        self.invoke(site={"controlPlane": {"nodeSelector": {}}, "runtime": {STORAGE: "csi"}})
        self.assertEqual(json.loads(self.output.read_text()), PLACEMENT)

    def test_every_conflict_refuses_without_echoing_the_label_or_storage_value(self):
        for section, fields in PLACEMENT.items():
            for field, value in fields.items():
                with self.subTest(section=section, field=field):
                    conflict = SECRET if field == STORAGE else {next(iter(value)): ""}
                    site = {**EMPTY_SITE, section: {field: conflict}}
                    self.invoke(site=site, refusal=f"Site profile.{section}.{field} conflicts")
                    self.assertFalse(self.output.exists())

    def test_rejects_bad_site_before_invoking_terraform(self):
        for site, message in [
            ('{"extra":{"x":1,"x":2}}', "duplicate JSON keys"), ('{"extra":NaN}', "valid JSON"),
            ({"runtime": {}}, "controlPlane must be a JSON object"),
            ({"controlPlane": {}, "runtime": []}, "runtime must be a JSON object"),
            ({**EMPTY_SITE, "runtime": {"nodeSelector": None}}, "nodeSelector must be a JSON object"),
        ]:
            with self.subTest(message=message):
                self.invoke(site=site, race=True, refusal=message)
                self.assertFalse(self.output.exists())  # The fake would create this file if called.

    def test_rejects_untrusted_terraform_output(self):
        runtime = PLACEMENT["runtime"]
        for placement, message in [
            ({**PLACEMENT, SECRET: 1}, "unexpected fields"),
            ({**PLACEMENT, "runtime": {**runtime, SECRET: 1}}, "oce_placement.runtime contains unexpected fields"),
            ({**PLACEMENT, "runtime": {}}, "gatewayNodeSelector is required"),
            ({**PLACEMENT, "controlPlane": {"nodeSelector": {}}}, "at least one selector"),
            ({**PLACEMENT, "runtime": {**runtime, "nodeSelector": {SECRET: 5}}}, "only string selector values"),
            ({**PLACEMENT, "runtime": {**runtime, STORAGE: " "}}, "nonempty string"),
            ('{"runtime":{"x":1,"x":2}}', "duplicate JSON keys"), (SECRET, "oce_placement must contain valid JSON"),
        ]:
            with self.subTest(message=message):
                self.invoke(placement=placement, refusal=message)
                self.assertFalse(self.output.exists())
        self.invoke(exit_code=1, refusal="Terraform could not read oce_placement")
        self.assertFalse(self.output.exists())

    def test_requires_private_output_directory(self):
        self.output.parent.chmod(0o750)
        self.invoke(race=True, refusal="directory owned by you with mode 0700")
        self.assertFalse(self.output.exists())

    def test_does_not_overwrite_an_existing_file_dangling_link_or_concurrent_writer(self):
        self.output.write_text("original")
        self.invoke(race=True, refusal="--output already exists")
        self.assertEqual(self.output.read_text(), "original")
        self.output.unlink()
        self.output.symlink_to(self.root / "missing")
        self.invoke(refusal="--output already exists")
        self.assertEqual(self.output.readlink(), self.root / "missing")
        self.assertFalse(self.output.exists())
        self.output.unlink()
        self.invoke(race=True, refusal="--output already exists")
        self.assertEqual(self.output.read_text(), "competing writer")

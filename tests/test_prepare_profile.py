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
CLUSTER = {
    "region": "us-west-2", "name": "example", "arn": "arn:aws:eks:us-west-2:123456789012:cluster/example",
}
API_CIDRS = ["10.0.0.42/32", "172.20.0.1/32", "192.168.0.4/32"]
INVENTORY = {
    "checkedAt": "2026-11-22T13:00:00.000Z", "context": "oce-example", "apiCidrs": API_CIDRS,
    "eks": {"clusterArn": CLUSTER["arn"], "webhookCidrs": ["10.0.2.1/32"]},
}
PLACEMENT_READ = ("oce_placement",)
EKS_READS = ("oce_cluster", "oce_placement")
FAKE = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
caller = Path(__file__).resolve().parent
assert Path.cwd() == caller
output = sys.argv[-1]
assert sys.argv[1:-1] == [f"-chdir={caller / 'target with spaces'}", "output", "-json"]
assert output in ("oce_placement", "oce_cluster")
assert os.path.samestat(os.fstat(0), os.stat(os.devnull))
with Path("reads").open("a") as reads:
    print(output, file=reads)
if output == "oce_placement" and os.environ["RACE"] == "1":
    Path("out/new").write_text("competing writer")
sys.stdout.write(os.environ[output])
sys.stderr.write(os.environ["SECRET"])
sys.exit(int(os.environ[output + "_exit"]))
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

    def invoke(
        self, *, site=EMPTY_SITE, placement=PLACEMENT, cluster=CLUSTER, inventory=None,
        missing_inventory=False, refusal=None, exit_code=0, cluster_exit_code=0, race=False, reads=None,
    ):
        raw = site if isinstance(site, str) else json.dumps(site)
        self.input.write_text(raw)
        log = self.root / "reads"
        log.unlink(missing_ok=True)
        inventory_file = self.root / f"{SECRET}-inventory.json"
        command = [SCRIPT, "--target-dir", "target with spaces", "--input", self.input.name, "--output", "out/new"]
        if inventory is not None or missing_inventory:
            command.extend(["--eks-inventory", inventory_file.name])
        if inventory is not None:
            inventory_raw = inventory if isinstance(inventory, str) else json.dumps(inventory)
            inventory_file.write_text(inventory_raw)
        env = {
            "PATH": str(self.root), "SECRET": SECRET, "RACE": str(int(race)),
            "oce_placement": placement if isinstance(placement, str) else json.dumps(placement),
            "oce_cluster": cluster if isinstance(cluster, str) else json.dumps(cluster),
            "oce_placement_exit": str(exit_code), "oce_cluster_exit": str(cluster_exit_code),
        }
        result = subprocess.run(
            command, cwd=self.root, env=env, input=SECRET, text=True, capture_output=True, timeout=5,
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.input.read_text(), raw)
        if inventory is not None:
            self.assertEqual(inventory_file.read_text(), inventory_raw)
        if reads is not None:
            self.assertEqual(tuple(log.read_text().splitlines()) if log.exists() else (), reads)
        self.assertFalse(list(self.output.parent.glob(".oce-*")), "left a staging file")
        if refusal is None:
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
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

    def test_default_preserves_manual_api_cidrs_and_reads_only_placement(self):
        manual = ["10.43.0.1/32"]
        self.invoke(site={**EMPTY_SITE, "controlPlane": {"clusterCidrs": manual}},
                    reads=PLACEMENT_READ)
        self.assertEqual(json.loads(self.output.read_text())["controlPlane"]["clusterCidrs"], manual)

    def test_eks_inventory_adds_only_api_peers_to_the_profile(self):
        self.invoke(inventory=INVENTORY, reads=EKS_READS)
        self.assertEqual(json.loads(self.output.read_text()), {
            **PLACEMENT, "controlPlane": {**PLACEMENT["controlPlane"], "clusterCidrs": API_CIDRS},
        })

    def test_eks_inventory_preserves_the_site_api_peer_order(self):
        original = list(reversed(API_CIDRS))
        site = {**EMPTY_SITE, "controlPlane": {"clusterCidrs": original}}
        self.invoke(site=site, inventory=INVENTORY, reads=EKS_READS)
        self.assertEqual(json.loads(self.output.read_text())["controlPlane"]["clusterCidrs"], original)

    def assert_inventory_refused(self, refusal, *, inventory=INVENTORY, reads=("oce_cluster",), **kwargs):
        self.invoke(refusal=refusal, inventory=inventory, reads=reads, **kwargs)
        self.assertFalse(self.output.exists())

    def test_eks_inventory_refuses_conflicting_site_peers(self):
        for existing in [
            None, [], API_CIDRS[:-1], API_CIDRS + ["10.0.2.1/32"], API_CIDRS + API_CIDRS[:1],
            ["10.0.0.0/24", *API_CIDRS[1:]], [SECRET, *API_CIDRS[1:]], [{}, *API_CIDRS[1:]],
        ]:
            with self.subTest(existing=existing):
                self.assert_inventory_refused(
                    "Site profile.controlPlane.clusterCidrs conflicts with EKS inventory apiCidrs",
                    site={**EMPTY_SITE, "controlPlane": {"clusterCidrs": existing}}, reads=(),
                )

    def test_eks_inventory_requires_distinct_canonical_private_ipv4_host_peers(self):
        for cidrs in [
            SECRET, [], [10], API_CIDRS + API_CIDRS[:1], ["10.0.0.42"], ["10.0.0.42/032"],
            ["10.0.0.42/255.255.255.255"], ["010.0.0.42/32"], ["10.0.0.0/24"], ["172.32.0.1/32"],
            ["169.254.169.253/32"], ["100.64.0.1/32"], ["127.0.0.1/32"], ["fd00::1/32"], [SECRET],
        ]:
            with self.subTest(cidrs=cidrs):
                self.assert_inventory_refused("apiCidrs must contain distinct private IPv4 /32",
                                              inventory={**INVENTORY, "apiCidrs": cidrs}, reads=())

    def test_eks_inventory_rejects_unreadable_or_malformed_snapshots(self):
        self.assert_inventory_refused("Could not read --eks-inventory",
                                      inventory=None, missing_inventory=True, reads=())
        for inventory, message in [
            (SECRET, "must contain valid JSON"), ({}, "nonempty JSON object"), ([], "nonempty JSON object"),
            ('{"eks":{"clusterArn":1,"clusterArn":2}}', "duplicate JSON keys"),
            ({**INVENTORY, "eks": {}}, "needs eks.clusterArn"),
            ({**INVENTORY, "checkedAt": None}, "valid UTC checkedAt"),
            ({**INVENTORY, "checkedAt": "2026-11-31T13:00:00.000Z"}, "valid UTC checkedAt"),
            ({**INVENTORY, "checkedAt": "2026-11-22T13:00:00-08:00"}, "valid UTC checkedAt"),
        ]:
            with self.subTest(inventory=inventory):
                self.assert_inventory_refused(message, inventory=inventory, reads=())

    def test_eks_inventory_rejects_a_different_account_or_context(self):
        other_arn = CLUSTER["arn"].replace("123456789012", "210987654321")
        for override in [{"context": SECRET}, {"eks": {"clusterArn": other_arn}}]:
            with self.subTest(override=override):
                self.assert_inventory_refused("cluster ARN and context must match", inventory={**INVENTORY, **override})

    def test_eks_inventory_requires_a_matching_applied_eks_output(self):
        for cluster in [
            # Both non-EKS targets export oce_cluster with a different shape.
            {"id": "/subscriptions/example", "location": "westus", "name": "example"},
            {"project_id": "example", "location": "us-west1-a", "name": "example"},
            [], {**CLUSTER, "arn": SECRET}, {**CLUSTER, "region": "us-east-2"}, {**CLUSTER, "name": "other"},
        ]:
            with self.subTest(cluster=cluster):
                self.assert_inventory_refused("oce_cluster must identify an applied AWS EKS cluster", cluster=cluster)
        self.assert_inventory_refused("oce_cluster has duplicate JSON keys", cluster='{"name":"example","name":"other"}')
        self.assert_inventory_refused("oce_cluster must contain valid JSON", cluster=SECRET)
        self.assert_inventory_refused("Terraform could not read oce_cluster", cluster=SECRET, cluster_exit_code=1)

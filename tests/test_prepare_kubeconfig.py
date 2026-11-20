"""Exercise the operator entry point with recorded-shaped AWS responses and real kubectl."""

import base64
import json
import os
import shutil
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare-kubeconfig"
REGION = "us-east-2"
NAME = "oce-evaluation"
ARN = f"arn:aws:eks:{REGION}:123456789012:cluster/{NAME}"
CONTEXT = f"oce-{NAME}"
ENDPOINT = "https://ABC0123.gr7.eks.us-east-2.amazonaws.com"
CA = base64.b64encode(b"synthetic public CA data").decode()

# The subprocess fixture implements the documented CLI shapes. kubectl is real and
# parses the supplied kubeconfig without connecting to a cluster.
CLI_FIXTURE = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

assert os.environ["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"] == "true"
command = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ["OCE_TEST_FIXTURE_DIRECTORY"])
if command == "aws" and "get-token" in args:
    (root / "plugin-ran").touch()
    sys.exit(79)
if command == "terraform" and args == ["output", "-json", "oce_cluster"]:
    phase = "output"
elif command == "kubectl":
    assert args[0] == "--kubeconfig" and args[2:5] == ["--context", "oce-oce-evaluation", "--request-timeout=15s"], args
    assert Path(args[1]).parent.parent == root and Path(args[1]).is_file(), args
    args = args[5:]
    if args[0] == "config" or os.environ.get("OCE_TEST_REAL_CLUSTER_READ") == "true":
        # Let stock kubectl parse the file we may later use for cluster reads.
        os.execv(os.environ["OCE_TEST_REAL_KUBECTL"], [os.environ["OCE_TEST_REAL_KUBECTL"], *sys.argv[1:]])
    phases = {
        ("get", "nodes", "-o", "json"): "nodes",
        ("-n", "kube-system", "get", "daemonset/aws-node", "daemonset/kube-proxy", "daemonset/ebs-csi-node",
         "deployment/ebs-csi-controller", "deployment/coredns", "configmap/amazon-vpc-cni", "-o", "json"): "workloads",
        ("get", "storageclass", "-o", "json"): "storage",
    }
    phase = phases[tuple(args)]
else:
    cluster = json.loads((root / "output.json").read_text())
    prefix = ["--region", cluster["region"], "--no-cli-pager", "--no-cli-auto-prompt", "eks"]
    assert command == "aws" and args[:len(prefix)] == prefix, (command, args)
    operation, *arguments = args[len(prefix):]
    if operation == "describe-cluster":
        assert arguments == ["--name", cluster["name"], "--output", "json"], arguments
        phase = "describe"
    else:
        assert operation == "update-kubeconfig", operation
        assert arguments[:5] == ["--name", cluster["name"], "--alias", "oce-" + cluster["name"], "--kubeconfig"], arguments
        assert arguments[6:] == ["--dry-run"], arguments
        assert Path(arguments[5]).parent.parent == root and not Path(arguments[5]).exists()
        phase = "generated"
with (root / "calls").open("a") as transcript:
    print(phase, file=transcript)
print((root / (phase + ".json")).read_text())
"""


class PrepareKubeconfigTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="oce-kubeconfig-test-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.destination = self.root / "kubeconfig"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fixture = self.bin / "fixture"
        fixture.write_text(CLI_FIXTURE)
        fixture.chmod(0o500)
        self.kubectl = shutil.which("kubectl")
        self.assertIsNotNone(self.kubectl, "kubectl is required for the handoff tests")
        for cli in ("aws", "terraform", "kubectl"):
            (self.bin / cli).symlink_to(fixture)

        self.cluster = {
            "region": REGION,
            "name": NAME,
            "arn": ARN,
            "endpoint": ENDPOINT,
            "certificate_authority_data": CA,
        }
        self.description = {
            "name": NAME,
            "arn": ARN,
            "endpoint": ENDPOINT,
            "certificateAuthority": {"data": CA},
            "status": "ACTIVE",
        }
        self.generated = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": CONTEXT,
            "clusters": [
                {"name": ARN, "cluster": {"server": ENDPOINT, "certificate-authority-data": CA}}
            ],
            "contexts": [{"name": CONTEXT, "context": {"cluster": ARN, "user": ARN}}],
            "users": [
                {"name": ARN, "user": {"exec": {
                    "apiVersion": "client.authentication.k8s.io/v1beta1",
                    "command": "aws",
                    "args": ["--region", REGION, "eks", "get-token", "--cluster-name", NAME, "--output", "json"],
                }}}
            ],
        }
        self.nodes = [
            {
                "metadata": {"labels": {
                    "oce-role": pool, "eks.amazonaws.com/nodegroup": pool,
                    "topology.kubernetes.io/zone": zone,
                }},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
            for pool, zone in (("control", "us-east-2a"), ("agents", "us-east-2b"))
        ]
        daemon_status = {
            "observedGeneration": 2, "desiredNumberScheduled": 2,
            "numberReady": 2, "numberAvailable": 2, "updatedNumberScheduled": 2,
        }
        deploy_status = {
            "observedGeneration": 2, "replicas": 1, "availableReplicas": 1,
            "updatedReplicas": 1, "readyReplicas": 1,
        }
        self.workloads = [
            {"metadata": {"name": name, "generation": 2}, "status": counters.copy()}
            for name, counters in (("aws-node", daemon_status), ("ebs-csi-node", daemon_status),
                                   ("ebs-csi-controller", deploy_status), ("coredns", deploy_status),
                                   ("kube-proxy", daemon_status))
        ]
        self.workloads[0]["spec"] = {"template": {"spec": {"containers": [
            {"name": "aws-node", "env": [{"name": "NETWORK_POLICY_ENFORCING_MODE", "value": "strict"}]},
            {"name": "aws-eks-nodeagent", "args": ["--enable-network-policy=true"]},
        ]}}}
        self.workloads.append({
            "metadata": {"name": "amazon-vpc-cni"}, "data": {"enable-network-policy-controller": "true"},
        })
        self.storage = [{
            "metadata": {
                "name": "encrypted-gp3",
                "annotations": {"storageclass.kubernetes.io/is-default-class": "true"},
            },
            "provisioner": "ebs.csi.aws.com", "parameters": {"type": "gp3", "encrypted": "true"},
            "volumeBindingMode": "WaitForFirstConsumer", "reclaimPolicy": "Retain",
        }]
        self.profile = {"KUBECTL_KUBERC": "false"}
        self.default = self.root / "existing-default-kubeconfig"
        self.default.write_text("existing kubeconfig")

    def prepare(self, *args):
        for phase, value in [
            ("output", self.cluster),
            ("describe", {"cluster": self.description}),
            ("generated", self.generated),
            ("nodes", {"items": self.nodes}),
            ("workloads", {"items": self.workloads}),
            ("storage", {"items": self.storage}),
        ]:
            (self.root / f"{phase}.json").write_text(json.dumps(value))
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "KUBECONFIG": str(self.default),
            "OCE_TEST_FIXTURE_DIRECTORY": str(self.root),
            "OCE_TEST_REAL_KUBECTL": self.kubectl,
            **self.profile,
        }
        result = subprocess.run(
            [os.fspath(SCRIPT), *args, os.fspath(self.destination)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(self.default.read_text(), "existing kubeconfig")
        self.assertEqual(list(self.root.glob(".oce-kubeconfig-*")), [])
        return result

    def calls(self):
        transcript = self.root / "calls"
        return transcript.read_text().splitlines() if transcript.exists() else []

    def test_publish_selected_context_and_verified_ca_in_a_private_file(self):
        result = self.prepare()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), CONTEXT)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        view = json.loads(self.destination.read_text())
        self.assertEqual(view["clusters"], self.generated["clusters"])
        self.assertEqual(view["contexts"], self.generated["contexts"])
        token = view["users"][0]["user"]["exec"]
        self.assertEqual(token["command"], "aws")
        self.assertEqual(token["args"], self.generated["users"][0]["user"]["exec"]["args"])
        self.assertEqual(self.calls(), ["output", "describe", "generated"])

    def test_refuse_same_cluster_name_in_a_different_aws_account(self):
        self.description["arn"] = f"arn:aws:eks:{REGION}:987654321098:cluster/{NAME}"
        result = self.prepare()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match Terraform", result.stderr)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.calls(), ["output", "describe"])

    def test_refuse_generated_kubeconfig_with_a_different_ca(self):
        cluster = self.generated["clusters"][0]["cluster"]
        cluster["certificate-authority-data"] = base64.b64encode(b"different synthetic CA").decode()
        result = self.prepare()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the verified EKS cluster", result.stderr)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.calls(), ["output", "describe", "generated"])

    def test_refuse_http_before_aws_calls(self):
        self.cluster["endpoint"] = ENDPOINT.replace("https:", "http:")
        result = self.prepare()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTPS", result.stderr)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.calls(), ["output"])

    def test_refuse_existing_symlink_without_calling_aws(self):
        self.destination.symlink_to(self.default)
        result = self.prepare()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(self.calls(), [])

    def assert_blocked(self, message, phases):
        result = self.prepare("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.calls(), ["output", "describe", "generated", *phases])

    def test_checked_handoff_publishes_context_and_reports_node_placement(self):
        result = self.prepare("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), CONTEXT)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertIn("Ready control nodes by zone: us-east-2a=1", result.stderr)
        self.assertIn("Ready agents nodes by zone: us-east-2b=1", result.stderr)
        self.assertEqual(self.calls(), ["output", "describe", "generated", "nodes", "workloads", "storage"])

    def test_check_requires_explicit_preference_opt_out_before_exec(self):
        # Stock kubectl consults the operator's default kuberc. The synthetic
        # AWS token command records a marker before it returns without a token.
        (self.root / ".kube").mkdir()
        (self.root / ".kube" / "kuberc").write_text(json.dumps({
            "apiVersion": "kubectl.config.k8s.io/v1beta1", "kind": "Preference",
            "credentialPluginPolicy": "DenyAll",
        }))
        self.profile = {"HOME": str(self.root), "OCE_TEST_REAL_CLUSTER_READ": "true"}
        ca = subprocess.run(
            ["openssl", "req", "-new", "-x509", "-nodes", "-days", "1", "-newkey", "rsa:2048",
             "-subj", "/CN=credential-policy-test.invalid", "-keyout", str(self.root / "unused.key")],
            check=True, capture_output=True,
        ).stdout
        ca_data = base64.b64encode(ca).decode()
        self.cluster["certificate_authority_data"] = ca_data
        self.description["certificateAuthority"]["data"] = ca_data
        self.generated["clusters"][0]["cluster"]["certificate-authority-data"] = ca_data

        generated = self.prepare()
        self.assertEqual(generated.returncode, 0, generated.stderr)
        env = {**self.profile, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "OCE_TEST_FIXTURE_DIRECTORY": str(self.root), "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true"}
        command = [self.kubectl, "--kubeconfig", str(self.destination), "--context", CONTEXT,
                   "get", "--raw=/readyz", "--request-timeout=1s"]
        denied = subprocess.run(command, env=env, capture_output=True, text=True, timeout=8)
        self.assertIn("not allowed", denied.stderr)
        marker = self.root / "plugin-ran"
        self.assertFalse(marker.exists())
        permitted = subprocess.run(
            command, env={**env, "KUBECTL_KUBERC": "false"}, capture_output=True, timeout=8,
        )
        self.assertNotEqual(permitted.returncode, 0)
        self.assertTrue(marker.exists(), "the exec command is reachable with opted-out preferences")
        marker.unlink()
        self.destination.unlink()
        (self.root / "calls").unlink()
        result = self.prepare("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential owner", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.destination.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(self.calls(), [])


    def test_check_refuses_altered_exec_plugin_before_cluster_reads(self):
        token = self.generated["users"][0]["user"]["exec"]
        token["env"] = [{"name": "AWS_ENDPOINT_URL_STS", "value": "https://example.invalid"}]
        self.assert_blocked("does not match the verified EKS cluster", [])

    def test_check_accepts_the_stock_profile_exec_environment(self):
        self.profile["AWS_PROFILE"] = "operator"
        token = self.generated["users"][0]["user"]["exec"]
        token["env"] = [{"name": "AWS_PROFILE", "value": "operator"}]
        result = self.prepare("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), CONTEXT)

    def test_check_refuses_proxy_in_generated_cluster(self):
        self.generated["clusters"][0]["cluster"]["proxy-url"] = "http://proxy.example.invalid"
        self.assert_blocked("does not match the verified EKS cluster", [])

    def test_check_refuses_unready_agent_pool(self):
        self.nodes[1]["status"]["conditions"][0]["status"] = "False"
        self.assert_blocked("No Ready, schedulable agents node", ["nodes"])

    def test_check_refuses_cni_in_standard_mode(self):
        self.workloads[0]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "standard"
        self.assert_blocked("aws-node in strict mode", ["nodes", "workloads"])

    def test_check_refuses_disabled_policy_controller(self):
        self.workloads[-1]["data"]["enable-network-policy-controller"] = "false"
        self.assert_blocked("VPC CNI needs network policy enabled", ["nodes", "workloads"])

    def test_check_refuses_rolling_ebs_node_daemonset(self):
        self.workloads[1]["status"]["updatedNumberScheduled"] = 1
        self.assert_blocked("daemonset/ebs-csi-node rollout is incomplete", ["nodes", "workloads"])

    def test_check_refuses_competing_legacy_default_storageclass(self):
        self.storage.append({"metadata": {
            "name": "legacy",
            "annotations": {"storageclass.beta.kubernetes.io/is-default-class": "true"},
        }})
        self.assert_blocked("single default StorageClass", ["nodes", "workloads", "storage"])

    def test_check_refuses_unencrypted_gp3(self):
        self.storage[0]["parameters"]["encrypted"] = "false"
        self.assert_blocked("encrypted EBS gp3", ["nodes", "workloads", "storage"])


if __name__ == "__main__":
    unittest.main()

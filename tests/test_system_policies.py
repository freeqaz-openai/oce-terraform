import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "system-policies.py"
ARN = "arn:aws:eks:us-east-2:123456789012:cluster/oce-evaluation"
CONTEXT = "oce-oce-evaluation"
ENDPOINT = "https://example.eks.us-east-2.amazonaws.com"

FIXTURE = """
import json
from pathlib import Path
import sys

command = Path(sys.argv[0]).name
args = sys.argv[1:]
if command == "terraform":
    assert args == ["output", "-json", "system_network"]
    source = "network"
else:
    assert args[0] == "--kubeconfig"
    assert args[2:5] == ["--context", "oce-oce-evaluation", "--request-timeout=15s"]
    if args[5:] == ["config", "view", "--minify", "--raw", "-o", "json"]:
        assert args[1] == "config"
        source = "config"
    else:
        assert args[5:] == ["-n", "kube-system", "get", "service", "kube-dns", "-o", "json"]
        staged = Path(args[1])
        assert staged.name == "kubeconfig" and args[1] != "config"
        assert json.loads(staged.read_text()) == json.loads(Path("config.json").read_text())
        assert staged.stat().st_mode & 0o777 == 0o600
        source = "dns"
with Path("calls").open("a") as calls:
    calls.write(source + "\\n")
print(Path(source + ".json").read_text())
"""


def ip_ports(policy):
    return {
        (peer["ipBlock"]["cidr"], port["protocol"], port["port"])
        for rule in policy["egress"]
        for peer in rule["to"]
        if "ipBlock" in peer
        for port in rule["ports"]
    }


class SystemPolicyTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="oce-system-policy-test-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        for command in ("terraform", "kubectl"):
            fixture = self.root / command
            fixture.write_text(f"#!{sys.executable}\n{FIXTURE}")
            fixture.chmod(0o500)
        self.network = {
            "cluster_arn": ARN,
            "endpoint": ENDPOINT,
            "ca_data": "Y2E=",
            "resolver_ipv4": ["10.42.0.2", "169.254.169.253"],
            "ec2_endpoint_ipv4": ["10.42.255.229", "10.42.255.245"],
            "sts_endpoint_ipv4": ["10.42.255.230", "10.42.255.246"],
        }
        self.inventory = {
            "context": CONTEXT,
            "eks": {"clusterArn": ARN},
            "apiCidrs": ["172.20.0.1/32", "10.42.16.10/32"],
        }
        self.connection = {"server": ENDPOINT, "certificate-authority-data": "Y2E="}
        self.dns = {
            "selector": {"k8s-app": "kube-dns"},
            "ports": [{"port": 53, "protocol": protocol} for protocol in ("UDP", "TCP")],
            "clusterIP": "172.20.0.10",
        }

    def render(self, opt_out=True, token_command="aws"):
        config = {
            "current-context": CONTEXT,
            "clusters": [{"name": ARN, "cluster": self.connection}],
            "contexts": [{"name": CONTEXT, "context": {"cluster": ARN, "user": ARN}}],
            "users": [{"name": ARN, "user": {"exec": {
                "apiVersion": "client.authentication.k8s.io/v1beta1",
                "command": token_command,
                "args": ["--region", "us-east-2", "eks", "get-token",
                         "--cluster-name", "oce-evaluation", "--output", "json"],
            }}}],
        }
        fixtures = {
            "network": self.network,
            "inventory": self.inventory,
            "config": config,
            "dns": {"spec": self.dns},
        }
        for name, fixture in fixtures.items():
            (self.root / f"{name}.json").write_text(json.dumps(fixture))
        (self.root / "calls").unlink(missing_ok=True)
        env = {**os.environ, "PATH": f"{self.root}:{os.environ['PATH']}"}
        env.pop("KUBECTL_KUBERC", None)
        if opt_out:
            env["KUBECTL_KUBERC"] = "false"
        return subprocess.run(
            [sys.executable, EXAMPLE, "--inventory", "inventory.json", "--kubeconfig", "config", "--context", CONTEXT],
            cwd=self.root, env=env,
            capture_output=True, check=False, text=True, timeout=10,
        )

    def test_rendered_policies_scope_aws_access_to_the_controller(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        policies = json.loads(result.stdout)["items"]
        self.assertEqual({policy["metadata"]["namespace"] for policy in policies}, {"kube-system"})
        dns, controller, node = (policy["spec"] for policy in policies)
        self.assertEqual(
            [policy["podSelector"]["matchLabels"] for policy in (dns, controller, node)],
            [{"k8s-app": "kube-dns"}, {"app": "ebs-csi-controller"}, {"app": "ebs-csi-node"}],
        )
        self.assertEqual(dns["ingress"], [{
            "from": [{"namespaceSelector": {}}],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        }])
        api = {("172.20.0.1/32", "TCP", 443), ("10.42.16.10/32", "TCP", 443)}
        resolvers = {(ip + "/32", protocol, 53) for ip in ["10.42.0.2", "169.254.169.253"] for protocol in ("TCP", "UDP")}
        cluster_dns = {("172.20.0.10/32", protocol, 53) for protocol in ("TCP", "UDP")}
        aws = {("10.42.255." + ip + "/32", "TCP", 443) for ip in ("229", "245", "230", "246")}
        self.assertEqual(ip_ports(dns), api | resolvers)
        self.assertEqual(ip_ports(controller), api | cluster_dns | aws)
        self.assertEqual(ip_ports(node), api | cluster_dns)
        self.assertEqual(controller["ingress"], [])
        self.assertEqual(node["ingress"], [])

    def test_rejects_inventory_from_another_cluster_before_kubernetes_read(self):
        self.inventory["eks"]["clusterArn"] = ARN.replace("123456789012", "111111111111")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["network"])

    def test_rejects_kubeconfig_with_another_ca_before_live_dns_read(self):
        self.connection["certificate-authority-data"] = "Y2hhbmdlZA=="
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["network", "config"])

    def test_refuses_credentials_without_explicit_preference_opt_out(self):
        result = self.render(opt_out=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential owner", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.root / "calls").exists())

    def test_refuses_non_aws_plugin_before_live_dns_read(self):
        result = self.render(token_command="other-plugin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stock AWS token command", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["network", "config"])

    def test_bad_or_broad_peer_produces_no_manifest(self):
        for target, key, invalid in (
            (self.inventory, "apiCidrs", []),
            (self.inventory, "apiCidrs", ["10.42.0.0/16"]),
            (self.network, "resolver_ipv4", ["169.254.169.254"]),
            (self.network, "ec2_endpoint_ipv4", ["1.1.1.1"]),
            (self.network, "sts_endpoint_ipv4", ["2001:db8::10"]),
            (self.network, "sts_endpoint_ipv4", ["10.42.255.230"]),
        ):
            with self.subTest(key=key, invalid=invalid):
                old, target[key] = target[key], invalid
                try:
                    result = self.render()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                finally:
                    target[key] = old


if __name__ == "__main__":
    unittest.main()

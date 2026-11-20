#!/usr/bin/env python3
"""Render IPv4 NetworkPolicies for managed EKS DNS and EBS CSI."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eks_kubeconfig import parse_tunnel_port, require_kuberc_opt_out, verify_eks_view


def host_cidrs(values, *, amazon_resolver=False):
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("Expected a list of IPv4 addresses.")
    cidrs = set()
    for value in values:
        network = ipaddress.IPv4Network(value)
        peer = network.network_address
        if network.prefixlen != 32 or (
            not any(peer in block for block in PRIVATE_VPC_RANGES)
            and not (amazon_resolver and str(peer) == "169.254.169.253")
        ):
            raise ValueError("Use exact private IPv4 host addresses. The Amazon resolver also accepts 169.254.169.253.")
        cidrs.add(network)
    if not cidrs:
        raise ValueError("Every address list needs at least one peer.")
    return [{"ipBlock": {"cidr": str(peer)}} for peer in sorted(cidrs)]


PRIVATE_VPC_RANGES = tuple(
    ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
DNS = [{"protocol": protocol, "port": 53} for protocol in ("UDP", "TCP")]
HTTPS = [{"protocol": "TCP", "port": 443}]


def policy(name, labels, egress, ingress):
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"oce-system-{name}", "namespace": "kube-system"},
        "spec": {
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": ingress,
            "egress": egress,
        },
    }


def read_json(*command):
    # Subprocess errors can contain kubeconfig or credential diagnostics.
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, timeout=30,
            env={**os.environ, "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true"},
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError(f"Could not read JSON from {' '.join(command[:2])}.") from None


def system_inputs(args):
    require_kuberc_opt_out()
    network = read_json("terraform", "output", "-json", "system_network")
    inventory = json.loads(args.inventory.read_text())
    if (
        inventory["context"] != args.context
        or inventory["eks"]["clusterArn"] != network["cluster_arn"]
    ):
        raise ValueError("The API address inventory must match the applied EKS cluster and context.")

    kube = ("kubectl", "--kubeconfig", str(args.kubeconfig), "--context", args.context,
            "--request-timeout=15s")
    config = read_json(*kube, "config", "view", "--minify", "--raw", "-o", "json")
    verify_eks_view(
        config, args.context, network["cluster_arn"], network["endpoint"], network["ca_data"],
        tunnel_port=args.tunnel_port,
    )
    # Pin the inspected view so a subsequent read cannot load a changed exec plugin or server.
    with tempfile.TemporaryDirectory(prefix="oce-system-policies-") as directory:
        staged = Path(directory) / "kubeconfig"
        staged.write_text(json.dumps(config) + "\n")
        staged.chmod(0o600)
        kube = ("kubectl", "--kubeconfig", str(staged), "--context", args.context,
                "--request-timeout=15s")
        dns = read_json(*kube, "-n", "kube-system", "get", "service", "kube-dns", "-o", "json")["spec"]
    ports = {(port["protocol"], port["port"]) for port in dns["ports"]}
    if dns["selector"] != {"k8s-app": "kube-dns"} or not {("TCP", 53), ("UDP", 53)} <= ports:
        raise ValueError("The kube-dns Service must select the managed CoreDNS Pods on port 53.")
    return network, inventory["apiCidrs"], dns["clusterIP"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path, help="Fresh OCE EKS address inventory JSON file")
    parser.add_argument("--kubeconfig", required=True, type=Path, help="Private kubeconfig from prepare-kubeconfig")
    parser.add_argument("--context", required=True, help="Context printed by prepare-kubeconfig")
    parser.add_argument("--tunnel-port", type=parse_tunnel_port, metavar="PORT", help="Use the explicitly selected IPv4 loopback tunnel")
    args = parser.parse_args()

    try:
        network, api, dns_ip = system_inputs(args)
        api_rule = {"to": host_cidrs(api), "ports": HTTPS}
        dns_rule = {
            "to": [
                {"podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}},
                *host_cidrs([dns_ip]),
            ],
            "ports": DNS,
        }
        resolver_rule = {"to": host_cidrs(network["resolver_ipv4"], amazon_resolver=True), "ports": DNS}
        ec2_peers = host_cidrs(network["ec2_endpoint_ipv4"])
        sts_peers = host_cidrs(network["sts_endpoint_ipv4"])
        if len(ec2_peers) != 2 or len(sts_peers) != 2 or any(peer in ec2_peers for peer in sts_peers):
            raise ValueError("Applied state must have two distinct IPv4 interfaces for each AWS service.")
        aws_rule = {"to": ec2_peers + sts_peers, "ports": HTTPS}
    except ValueError as error:
        parser.error(str(error))
    except (OSError, KeyError, TypeError, IndexError):
        parser.error("Incomplete Terraform output, API inventory, kubeconfig, or kube-dns Service.")

    policies = [
        policy(
            "coredns",
            {"k8s-app": "kube-dns"},
            [api_rule, resolver_rule],
            [{"from": [{"namespaceSelector": {}}], "ports": DNS}],
        ),
        policy("ebs-controller", {"app": "ebs-csi-controller"}, [api_rule, dns_rule, aws_rule], []),
        policy("ebs-node", {"app": "ebs-csi-node"}, [api_rule, dns_rule], []),
    ]
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": policies}, indent=2))


if __name__ == "__main__":
    main()

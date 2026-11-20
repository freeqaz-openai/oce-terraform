import contextlib
import io
import json
from pathlib import Path
import runpy
from subprocess import CompletedProcess, TimeoutExpired
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-destroy-dependencies"
MAIN = runpy.run_path(str(SCRIPT))["main"]
ACCOUNT = "123456789012"
REGION = "us-east-1"
VPC = "vpc-0123456789abcdef0"
CLUSTER = "example-eks"
CLUSTER_TAG = "ebs.csi.aws.com/cluster-name"
VOLUME = "vol-0123456789abcdef1"
REPLICA = "sg-0123456789abcdef2"
DEFAULT_GROUP = "sg-0123456789abcdef3"
AWS_PREFIX = ("aws", "--no-cli-pager", "--region", REGION, "--output", "json")
PLAN_CALL = ("terraform", "show", "-json", "destroy.tfplan")
AWS_CALLS = (
    (*AWS_PREFIX, "sts", "get-caller-identity"),
    (*AWS_PREFIX, "ec2", "describe-vpcs", "--vpc-ids", VPC),
    (*AWS_PREFIX, "ec2", "describe-volumes", "--filters", f"Name=tag:{CLUSTER_TAG},Values={CLUSTER}"),
    (*AWS_PREFIX, "ec2", "describe-security-groups", "--filters", f"Name=vpc-id,Values={VPC}"),
)


def tags(key, value):
    return [{"Key": key, "Value": value}]


def delete_resource(resource_type, before):
    return {
        "address": f"{resource_type}.this",
        "mode": "managed",
        "type": resource_type,
        "change": {"actions": ["delete"], "before": before, "after": None},
    }


class CheckDestroyDependenciesTest(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "complete": True,
            "errored": False,
            "variables": {"aws_region": {"value": REGION}, "cluster_name": {"value": CLUSTER}},
            "resource_changes": [
                delete_resource("aws_vpc", {
                    "id": VPC, "arn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/{VPC}", "tags": {"Name": CLUSTER},
                }),
                delete_resource("aws_eks_cluster", {"name": CLUSTER, "vpc_config": [{"vpc_id": VPC}]}),
            ],
        }
        self.caller = {"Account": ACCOUNT}
        self.vpcs = {"Vpcs": [{"VpcId": VPC, "OwnerId": ACCOUNT, "Tags": tags("Name", CLUSTER)}]}
        self.volumes = {"Volumes": []}
        self.groups = {"SecurityGroups": [{
            "VpcId": VPC, "GroupId": DEFAULT_GROUP, "GroupName": "default", "Description": "default VPC group",
        }]}

    def run_check(self, expected_calls=(PLAN_CALL, *AWS_CALLS), failure=None):
        answers = dict(zip((PLAN_CALL, *AWS_CALLS), (self.plan, self.caller, self.vpcs, self.volumes, self.groups)))
        stdout = io.StringIO()
        stderr = io.StringIO()
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[0] == "aws":
                self.assertEqual(kwargs["env"]["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"], "true")
            if command == failure:
                raise TimeoutExpired(command, timeout=60, stderr=b"bound-token-sensitive-detail")
            return CompletedProcess(command, 0, json.dumps(answers[command]).encode())

        with (
            patch.object(MAIN.__globals__["subprocess"], "run", side_effect=run),
            patch("sys.argv", [str(SCRIPT), "destroy.tfplan"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                code = MAIN()
            except SystemExit as error:
                code = error.code
        self.assertEqual(tuple(calls), expected_calls)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_report_both_remaining_dependencies(self):
        self.volumes["Volumes"].append({
            "VolumeId": VOLUME, "Size": 40, "State": "available", "Tags": tags(CLUSTER_TAG, CLUSTER),
        })
        self.groups["SecurityGroups"].append({
            "VpcId": VPC, "GroupId": REPLICA, "GroupName": "FMManagedSecurityGroup00000000",
            "Description": "FMS managed security group",
        })
        code, stdout, stderr = self.run_check()
        self.assertEqual((code, stderr), (1, ""))
        self.assertIn(f"{VOLUME} (available, 40 GiB)", stdout)
        self.assertIn("Firewall Manager administrator", stdout)
        self.assertIn(REPLICA, stdout)
        self.assertNotIn(DEFAULT_GROUP, stdout)

    def test_clean_vpc_only_remainder_succeeds(self):
        self.plan["resource_changes"].pop()
        code, stdout, stderr = self.run_check()
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("No EBS CSI volume or Firewall Manager replica matches", stdout)

    def test_reject_plan_with_resource_replacement_before_aws_reads(self):
        self.plan["resource_changes"][1]["change"]["actions"] = ["delete", "create"]
        code, stdout, stderr = self.run_check(expected_calls=(PLAN_CALL,))
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("destroy plan", stderr)

    def test_reject_cluster_mismatch_before_aws_reads(self):
        cluster = self.plan["resource_changes"][1]["change"]["before"]
        for key, value in (
            ("name", "different-eks"),
            ("arn", f"arn:aws:eks:{REGION}:123456789013:cluster/{CLUSTER}"),
        ):
            with self.subTest(key=key), patch.dict(cluster, {key: value}):
                code, stdout, stderr = self.run_check(expected_calls=(PLAN_CALL,))
                self.assertEqual((code, stdout), (1, ""))
                self.assertIn("EKS cluster must match the VPC", stderr)

    def test_reject_wrong_account_before_resource_reads(self):
        self.caller["Account"] = "123456789013"
        code, stdout, stderr = self.run_check(expected_calls=(PLAN_CALL, AWS_CALLS[0]))
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("account in the saved plan", stderr)

    def test_reject_changed_vpc_tag_before_inventory_reads(self):
        self.vpcs["Vpcs"][0]["Tags"] = tags("Name", "different-eks")
        code, stdout, stderr = self.run_check(expected_calls=(PLAN_CALL, *AWS_CALLS[:2]))
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("current VPC identity and Name tag", stderr)

    def test_refuse_incomplete_inventory(self):
        self.groups["NextToken"] = "next-page"
        code, stdout, stderr = self.run_check()
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("incomplete or unexpected inventory", stderr)

    def test_fail_closed_without_printing_raw_cli_error(self):
        code, stdout, stderr = self.run_check(
            expected_calls=(PLAN_CALL, *AWS_CALLS[:3]), failure=AWS_CALLS[2],
        )
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("Could not list EBS CSI volumes", stderr)
        self.assertNotIn("bound-token-sensitive-detail", stderr)


if __name__ == "__main__":
    unittest.main()

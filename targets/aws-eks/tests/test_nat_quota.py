import contextlib
import io
from pathlib import Path
import runpy
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch


CHECK = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "check-nat-quota"),
)["check_nat_capacity"]


class CheckNatQuotaTest(unittest.TestCase):
    def make_plan(self, changes):
        return {
            "configuration": {"root_module": {"resources": [{"address": "aws_nat_gateway.this"}]}},
            "variables": {
                "aws_region": {"value": "us-east-1"},
                "operator_role_arn": {"value": "arn:aws:iam::123456789012:role/TestRole"},
                "availability_zones": {"value": ["us-east-1a", "us-east-1b"]},
            },
            "resource_changes": [
                {
                    "type": "aws_nat_gateway",
                    "name": "this",
                    "index": zone,
                    "change": {"actions": actions},
                }
                for zone, actions in changes
            ],
        }

    def check(self, plan, account="123456789012"):
        def fake_json(phase, *command):
            if command[0] == "terraform":
                self.assertEqual(command, ("terraform", "show", "-json", "test.tfplan"))
                return plan
            self.assertEqual(command[:4], ("aws", "--no-cli-pager", "--region", "us-east-1"))
            aws_call = command[6:8]
            if aws_call == ("sts", "get-caller-identity"):
                return {"Account": account}
            if aws_call == ("service-quotas", "get-service-quota"):
                return 5.0
            if aws_call == ("ec2", "describe-nat-gateways"):
                self.assertIn("Name=state,Values=pending,available,deleting", command)
                self.assertIn("NatGateways[?AvailabilityMode != 'regional'].SubnetId", command)
                return ["subnet-a"] * 5 + ["subnet-b"] * 4
            if aws_call == ("ec2", "describe-subnets"):
                self.assertEqual(command[8:11], ("--subnet-ids", "subnet-a", "subnet-b"))
                return [
                    {"id": "subnet-a", "zone": "us-east-1a"},
                    {"id": "subnet-b", "zone": "us-east-1b"},
                ]
            self.fail(f"Unexpected CLI call: {command[0]} {phase}")

        output = io.StringIO()
        with patch.dict(CHECK.__globals__, {"read_json": fake_json}), contextlib.redirect_stdout(output):
            CHECK("test.tfplan")
        return output.getvalue()

    def test_plan_with_full_zone_is_refused(self):
        plan = self.make_plan([("us-east-1a", ["create"]), ("us-east-1b", ["create"])])
        with self.assertRaisesRegex(ValueError, "exceeds the zonal NAT quota"):
            self.check(plan)

    def test_single_gateway_plan_can_fill_last_slot(self):
        plan = self.make_plan([("us-east-1b", ["create"])])
        self.assertIn("us-east-1b: 4/5 zonal NAT slots used, 1 requested", self.check(plan))

    def test_existing_gateways_at_quota_and_teardown_need_no_new_slots(self):
        for actions in (["no-op"], ["delete"], ["delete", "create"]):
            with self.subTest(actions=actions):
                plan = self.make_plan([("us-east-1a", actions)])
                self.assertEqual(
                    self.check(plan, account="012345678901"),
                    "No additional zonal NAT gateways in the plan.\n",
                )

    def test_create_before_delete_replacement_requires_an_extra_slot(self):
        plan = self.make_plan([("us-east-1a", ["create", "delete"])])
        with self.assertRaisesRegex(ValueError, "exceeds the zonal NAT quota"):
            self.check(plan)

    def test_wrong_account_is_refused(self):
        plan = self.make_plan([("us-east-1b", ["create"])])
        with self.assertRaisesRegex(ValueError, "account in the saved plan"):
            self.check(plan, account="012345678901")

    def test_unrelated_plan_is_refused(self):
        plan = {"configuration": {"root_module": {"resources": [{"address": "other.example"}]}}}
        with self.assertRaisesRegex(ValueError, "saved plan for the AWS EKS target"):
            self.check(plan)

    def test_option_is_refused_before_reading_plan(self):
        with patch.dict(CHECK.__globals__, {"read_json": lambda *_: self.fail("Read attempted")}):
            with self.assertRaisesRegex(ValueError, "Supply a saved plan path"):
                CHECK("-json")

    def test_quota_reads_use_aws_endpoints_even_with_custom_endpoint_configuration(self):
        read_json = CHECK.__globals__["read_json"]
        subprocess = read_json.__globals__["subprocess"]
        with (
            patch.dict("os.environ", {"AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "false"}),
            patch.object(subprocess, "run", return_value=CompletedProcess([], 0, b"{}")) as run,
        ):
            read_json("read the zonal NAT quota", "aws", "service-quotas", "get-service-quota")
        self.assertEqual(run.call_args.kwargs["env"]["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"], "true")


if __name__ == "__main__":
    unittest.main()

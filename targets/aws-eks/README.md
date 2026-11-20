# Prepare Amazon EKS for OpenClaw Enterprise

Use this Terraform reference to create a dedicated IPv4 Amazon Elastic
Kubernetes Service (EKS) cluster for OpenClaw Enterprise (OCE). It provisions a
VPC across two availability zones, private EC2 worker pools, EKS add-ons, and
operator access. Finish Kubernetes and database preparation, then use the OCE
checkout and OpenClaw Control Plane (OCC) CLI for installation.

Use a dedicated account or cluster approved for this evaluation. The OpenShell
Gateway receives cluster-wide Kubernetes permissions in operator mode.
[Read the OCE EKS requirements](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/eks-openshell-setup.md#prepare-aws-infrastructure).

## 1. Make an infrastructure plan

Install Terraform 1.10 or later, AWS CLI v2, Python 3, jq, and kubectl
compatible with the selected Kubernetes version. Keep an OCE checkout that matches
the images and chart you will install. Authenticate Terraform in the target
commercial AWS account. Use an existing IAM role in that account for Kubernetes
administration. The Terraform caller needs permission to provision the
resources in the plan, including IAM roles and the EKS access entry for that
operator role. Planning also requires `iam:GetRole`, `ec2:DescribeAvailabilityZones`,
`ec2:DescribeInstanceTypes`, and `ec2:DescribeInstanceTypeOfferings` to verify
the exact role, zones, and x86_64 node choices. Check EC2 launch capacity
and service quotas separately.

The default plan needs one free zonal NAT slot in each selected zone and two
Elastic IP addresses in the Region. Check the regional Elastic IP quota before
applying.

For a new cluster, select zones with capacity. For a disposable or cost-sensitive
cluster, you can set `single_nat_gateway_zone` to a selected zone with a free slot.
Both private subnets then share one NAT gateway and one Elastic IP address.
An outage in that zone interrupts internet access for both worker pools.
Traffic from the other zone can incur cross-zone charges.
[AWS NAT gateway availability](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-basics.html).

From this repository checkout, enter the AWS EKS target and create a private variables file.

```bash
set -euo pipefail
umask 077
cd "$(git rev-parse --show-toplevel)/targets/aws-eks"
TF_TARGET_DIR=$PWD
OCE_CHECKOUT=/absolute/path/to/openclaw-enterprise
export TF_TARGET_DIR OCE_CHECKOUT
cp examples/terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars before planning.
terraform init -lockfile=readonly
terraform validate
aws sts get-caller-identity
terraform plan -out=foundation.tfplan
./scripts/check-nat-quota foundation.tfplan
```

The saved-plan check requires
`servicequotas:GetServiceQuota`, `ec2:DescribeNatGateways`, and
`ec2:DescribeSubnets` in the plan's AWS account. It compares additional gateways
in the plan with current usage. Pending, available, and deleting gateways count
toward the quota. If a zone lacks capacity, select zones with free slots or use
the single-gateway option, then save and check a new plan. Other concurrent
changes can still consume the remaining capacity.
[AWS NAT gateway quotas and troubleshooting](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-troubleshooting.html#nat-gateway-troubleshooting-quota).

Check the account, region, availability zones, IP ranges, node counts, and
operator role before approval. Review charges for EKS, EC2 nodes, NAT gateways,
the EC2 and STS interface endpoints in both zones, EBS, data processing, and
logging. Keep state and saved plans private. For shared operation, configure an
access-controlled remote state backend before applying.

The Kubernetes API is private by default. Arrange HTTPS access from the VPC or
a connected network, including on the control plane security group. If you use
the public endpoint, set `api_public_access_cidrs` to the approved, stable IPv4
egress CIDRs for your operator. The workers still use the private endpoint.
[AWS endpoint access](https://docs.aws.amazon.com/eks/latest/userguide/cluster-endpoint.html).

```bash
terraform apply foundation.tfplan
```

## 2. Select the applied cluster

Use AWS credentials for the configured operator role and run from a host that
can reach the Kubernetes API. Keep the run directory for later verification
and cleanup. The handoff compares the applied Terraform output with the active
EKS cluster, then writes a new private kubeconfig. It prints the context name.

If you use AWS Systems Manager Session Manager for private access, start
port forwarding through a managed node that can reach the EKS API. Forward
the EKS endpoint hostname on port 443 to a local port other than 443. Add
`--tunnel-port PORT` to `prepare-kubeconfig` with that local port. Kubectl
connects to `127.0.0.1` and still verifies the EKS hostname and certificate
authority. Keep the session running and configure any local proxy to bypass
`127.0.0.1`.
[Start a Session Manager port-forwarding session to a remote host](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html#sessions-remote-port-forwarding).

```bash
umask 077
run_root=${XDG_STATE_HOME:-$HOME/.local/state}/oce
install -d -m 700 "$run_root"
RUN_DIR=$(mktemp -d "$run_root/eks.XXXXXXXX")
KUBECONFIG_FILE="$RUN_DIR/kubeconfig"
CONTEXT=$(./scripts/prepare-kubeconfig "$KUBECONFIG_FILE")
EKS_CLUSTER_ARN=$(terraform output -json oce_cluster | jq -er '.arn')
export RUN_DIR KUBECONFIG_FILE CONTEXT EKS_CLUSTER_ARN
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" get nodes -L oce-role
```

Both `oce-role=control` and `oce-role=agents` need Ready nodes. A successful
kubeconfig handoff verifies EKS identity and TLS inputs. The Kubernetes read
verifies that the current operator can reach and authenticate to the API.
If it times out, check your endpoint route or stable egress allowlist. If AWS
denies access or kubectl reports an authentication error, check the current identity,
the operator access entry, and `eks:DescribeCluster` permission.
[AWS kubeconfig requirements](https://docs.aws.amazon.com/eks/latest/userguide/create-kubeconfig.html).

## 3. Prepare networking, storage, and OCE dependencies

Review existing StorageClasses before creating the supplied encrypted default.
Resolve any existing default or existing `encrypted-gp3` class with its owner.
The class uses `WaitForFirstConsumer` for volume placement and retains the EBS
volume when its claim is deleted.

```bash
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" get storageclass
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" create -f examples/encrypted-gp3.yaml
```

Follow [Prepare the system add-ons for strict NetworkPolicy](docs/system-network-policies.md)
from the AWS EKS target directory. It collects the current Kubernetes API addresses,
renders policies from applied Terraform outputs, and prepares CoreDNS and EBS CSI
before you persist `strict_network_policy = true` and apply the reviewed plan.
Prepare the policies for your other controllers and admission webhooks before
that transition. Verify real DNS, allowed and denied traffic, and webhook
admission after the strict-mode rollout.

Provision and mount a disposable claim, and keep node capacity in its availability
zone. Install the reviewed runtime seccomp profile on every eligible Agent node,
including replacements. Prepare private TLS-verified PostgreSQL with separate app
and migrator roles, supported images and registry access, the Gateway and
certificate controllers, and the selected OCE profile. Keep application and model
credentials in the protected OCE inputs.
[OCE installation preparation](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-preparation.md) ·
[OCE compute requirements](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/reference/drivers/kubernetes-compute.md#requirements).

## 4. Check the foundation and install OCE

Run the foundation check from the AWS EKS target directory using the operator role. It checks
the EKS identity and CA, Ready schedulable nodes in both pools, strict VPC CNI
settings, add-on rollouts, and the encrypted default StorageClass. Review the
reported node counts by zone against your volume placement.

The check requires the credential owner's permission to use kubectl without
preferences. For an authorized session, set `KUBECTL_KUBERC=false` for the
check. If your policy requires kubectl preferences, use your approved
infrastructure workflow. Complete the traffic, admission, and volume checks
before installing OCE. When using a tunnel, pass the same `--tunnel-port PORT`
to the foundation check.

```bash
OCC_INPUT_DIRECTORY=$(mktemp -d "$RUN_DIR/occ.XXXXXXXX")
KUBECONFIG_FILE="$OCC_INPUT_DIRECTORY/kubeconfig"
CONTEXT=$(./scripts/prepare-kubeconfig --check "$KUBECONFIG_FILE")
export OCC_INPUT_DIRECTORY KUBECONFIG_FILE CONTEXT
cd "$OCE_CHECKOUT"
```

The command writes the kubeconfig directly to the protected OCE input directory
after the checks pass. If a check fails, correct the reported condition and
repeat the check before changing directories.

Continue in the same shell with the [Terraform handoff in the OCE guide](https://github.com/openclaw/openclaw-enterprise/blob/fb03480f67004e4b2498ca72d124b458c04744ac/docs/guides/deploy/production-installation-preparation.md#use-a-terraform-managed-eks-cluster).
It maps the node selectors, StorageClass and current addresses into the profile.
Use the installation procedure for your selected OCE release to prepare the
namespace and Secrets, check prerequisites, and run `occ installation install`.
Keep the receipt with the original run directory.

## Remove an evaluation cluster

Verify the account, region, cluster, and original state. Remove OCE workloads,
then settle their load balancers and retained EBS volumes with their owners.
Handle PostgreSQL cleanup through its own workflow. From the AWS EKS target directory,
review and apply a saved destruction plan.

```bash
cd "$TF_TARGET_DIR"
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

[OCE cleanup guide](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/eks-cleanup.md).

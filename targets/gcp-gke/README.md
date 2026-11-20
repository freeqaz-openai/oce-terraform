# GKE preview for OCE

This target plans a zonal GKE Standard cluster with private Ubuntu nodes, a
private Kubernetes API endpoint, Dataplane V2, and the Compute Engine
Persistent Disk CSI driver. It creates separate single-node AMD64 pools for
the OCE control plane and Agents. Credential-free Terraform tests cover the
plan. Live GKE provisioning and OCE qualification remain open.
[Network isolation](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/latest/network-isolation)
· [Dataplane V2](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dataplane-v2)

## Prepare Google Cloud

- Select a dedicated project with billing, the Compute Engine and GKE APIs,
  and quota for the control plane, VMs, and disks. Both pools run in the same
  zone. Use a supported Kubernetes version for your OCE release.
- In that project and region, provide an existing subnet with Private Google
  Access and distinct IPv4 secondary ranges for Pods and Services. Select a
  free control-plane /28 outside the subnet ranges and other connected
  networks. Provide outbound access for image pulls and OCE dependencies,
  such as a Cloud NAT gateway for the private subnet.
- Use a routed operator host in the region. Permit the host's source CIDR
  on the private Kubernetes API. Authenticate Terraform with Application
  Default Credentials authorized to create and delete GKE clusters and node
  pools and read the subnet. The Terraform identity also needs
  `iam.serviceAccounts.actAs` on the node service account.
- Supply a user-managed node service account with
  `roles/container.defaultNodeServiceAccount` in the project. Grant image
  repository read access where required. Use a dedicated GKE administrator
  for Kubernetes setup. Have your project and cluster administrators grant
  its IAM and Kubernetes API permissions before running the handoff.
  [Node service accounts](https://docs.cloud.google.com/kubernetes-engine/security/configure-node-service-accounts)
  · [GKE authorization](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/role-based-access-control)

## Plan and apply

From the GKE directory, copy `examples/terraform.tfvars.example` to `terraform.tfvars`,
fill in the actual values, and set `enable_preview = true`. This directory
owns its own state. Keep the state and saved plans private.

```bash
umask 077
terraform init -lockfile=readonly
terraform plan -out=preview.tfplan
terraform show preview.tfplan
```

Review the project, location, operator CIDRs, and resource costs. Applying
creates billable resources.

```bash
terraform apply preview.tfplan
```

## Prepare the cluster for OCE

Install the Google Cloud CLI, GKE auth plugin, kubectl, and jq on your routed
operator host. Use the GKE administrator identity and keep this kubeconfig separate
from other clusters.

```bash
set -euo pipefail
umask 077
OCC_INPUT_DIRECTORY=$(mktemp -d "${TMPDIR:-/tmp}/oce-gke.XXXXXXXX")
export OCC_INPUT_DIRECTORY
export KUBECONFIG_FILE="$OCC_INPUT_DIRECTORY/kubeconfig"
export KUBECONFIG="$KUBECONFIG_FILE"
target=$(terraform output -json oce_cluster)
name=$(jq -er '.name' <<<"$target")
project=$(jq -er '.project_id' <<<"$target")
zone=$(jq -er '.location' <<<"$target")
gcloud container clusters get-credentials "$name" --project "$project" --zone "$zone" --internal-ip
chmod 600 "$KUBECONFIG_FILE"
CONTEXT=$(kubectl --kubeconfig "$KUBECONFIG_FILE" config current-context)
export CONTEXT
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" get nodes -L oce-role,kubernetes.io/arch
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" apply -f examples/storage-class.yaml
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" get storageclass
```

Use the [placement output](../../README.md#configure-oce-placement) to set
the node selectors and Gateway StorageClass for the OCE installation. For
dedicated Agent claims, configure one default StorageClass that supports
40 GiB `ReadWriteOnce` PVCs with `WaitForFirstConsumer`. For example, make
`oce-gke-rwo` the default after clearing the default designation from any
other class. The example retains disks after claim deletion. Verify disk
placement and an actual mount before installing OCE.
[GKE disk CSI](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/gce-pd-csi-driver)

Use the OCE procedure to prepare the database, routing, and secrets. GKE
Dataplane V2 excludes Pod and Service addresses from NetworkPolicy `ipBlock`
rules. Use the actual private control-plane endpoint for API egress and supply
the private database destination addresses. The Kubernetes API Service IP from
the EKS inventory procedure cannot establish API egress on GKE. Verify allowed
and denied paths with the rendered OCE policies and check webhook admission from
the control plane before installation.
[GKE NetworkPolicy rules](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/network-policy#migrating_from_calico_to_gke_dataplane_v2)

Before admitting Agents, verify the OCE security requirements on the chosen
nodes and install any local seccomp profiles selected by the profile.
[OCE installation](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-preparation.md)
· [OCE Kubernetes compute requirements](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/reference/drivers/kubernetes-compute.md#requirements)

## Tear down

Remove the OCE release and review retained disks, cloud load balancers, and
external resources. Turn off `deletion_protection` in `terraform.tfvars`.
Review and apply a plan containing that setting change, then review a
destruction plan. Retained disks may continue to incur costs.

```bash
terraform plan -out=unprotect.tfplan
terraform apply unprotect.tfplan
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

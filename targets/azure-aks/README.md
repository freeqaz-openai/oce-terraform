# Azure AKS preview

Use this target to prepare a dedicated Azure Kubernetes Service (AKS) cluster for an OpenClaw Enterprise evaluation. Terraform plans have been tested with a mock provider. Live provisioning and the OCE installation still need qualification.

This root creates a resource group, a private IPv4 AKS cluster with AKS-managed networking and DNS, two system nodes labeled `oce-role=control`, and one user node labeled `oce-role=agents`. Both node pools use private addresses. The operator group gets Entra-backed Azure Kubernetes RBAC at cluster scope. Azure CNI uses Cilium for NetworkPolicy enforcement, and AKS enables Azure Disk CSI. Standard-tier AKS, VMs, disk volumes, and egress can incur charges. [Private AKS networking](https://learn.microsoft.com/en-us/azure/aks/private-clusters) · [Azure RBAC](https://learn.microsoft.com/en-us/azure/aks/entra-id-authorization)

## Plan and apply

1. Select a subscription and tenant where you can create a resource group, create an AKS cluster, and assign the two built-in roles to your operator group. Confirm that the AKS, Compute, Network, and Managed Identity resource providers are registered there. Terraform disables automatic registration.
2. Check the region's available Kubernetes releases with `az aks get-versions`. Choose a supported minor version at or above 1.35 and an amd64 VM SKU with enough capacity and quota for both pools.
3. Copy `examples/terraform.tfvars.example` to `terraform.tfvars`, replace the example values, and set `enable_preview = true` to proceed. Store this target's state and saved plans in protected storage. Run these commands from this directory with Azure credentials for the selected tenant and subscription.

```bash
terraform init -lockfile=readonly
terraform plan -out=aks.tfplan
terraform show aks.tfplan
```

Review the subscription, operator group, both labeled node pools, and costs in
the saved plan. Apply the reviewed plan when you are ready to provision AKS.

```bash
terraform apply aks.tfplan
```

## Check the cluster and hand it off

From an approved operator host with a route to the private API and access to its private DNS zone, sign in to the selected Entra tenant as a member of the operator group. For a host in another VNet, arrange peering and link or forward the AKS private DNS zone first. Install Azure CLI, `kubelogin`, `kubectl`, and `jq`. Local accounts and Azure Run Command are disabled.

```bash
set -euo pipefail
umask 077
cluster=$(terraform output -json oce_cluster)
subscription=$(jq -er '.subscription_id' <<<"$cluster")
resource_group=$(jq -er '.resource_group' <<<"$cluster")
name=$(jq -er '.name' <<<"$cluster")
CONTEXT="oce-aks-$name"
OCC_INPUT_DIRECTORY=$(mktemp -d "${TMPDIR:-/tmp}/oce-aks.XXXXXXXX")
KUBECONFIG_FILE="$OCC_INPUT_DIRECTORY/kubeconfig"

az aks get-credentials --subscription "$subscription" --resource-group "$resource_group" \
  --name "$name" --file "$KUBECONFIG_FILE" --context "$CONTEXT" --format exec
chmod 600 "$KUBECONFIG_FILE"
export CONTEXT OCC_INPUT_DIRECTORY KUBECONFIG_FILE

kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" get nodes -L oce-role
kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" get storageclass managed-csi-premium
```

Confirm both pools are Ready and verify that an encrypted persistent disk mounts in the intended pool. Use the [placement output](../../README.md#configure-oce-placement) to set the node selectors and Gateway StorageClass for the OCE installation. Confirm the default StorageClass also supports the persistent volume claims used by dedicated Agents. The built-in classes can delete their disks when their volumes are deleted. Verify allowed and denied traffic, including API, DNS, and private PostgreSQL traffic. Cilium's Kubernetes `ipBlock` rules cannot select Pod or node IPs. Use supported selectors for Pod peers and verify the effective paths before installation. [Azure Disk CSI](https://learn.microsoft.com/en-us/azure/aks/csi-storage-drivers) · [Cilium policy behavior](https://learn.microsoft.com/en-us/azure/aks/azure-cni-powered-by-cilium)

Continue with OCE installation preparation in your matching release. Supply your PostgreSQL service, images, application Secrets, and routing prerequisites there. [OCE installation preparation](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-preparation.md)

## Clean up

Back up any data you intend to keep outside the AKS-managed node resource group. Remove OCE workloads and confirm how Kubernetes will handle their persistent disks before destroying the cluster. AKS deletes the managed node resource group when the cluster is destroyed. Remove any operator-created peering and DNS links. Keep `enable_preview = true`, run `terraform plan -destroy -out=aks-destroy.tfplan` from this target, review the plan against the original subscription and state, then run `terraform apply aks-destroy.tfplan`.

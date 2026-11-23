# Terraform targets for OpenClaw Enterprise

Prepare cloud infrastructure for OpenClaw Enterprise (OCE), then install OCE with the OpenClaw Control Plane (OCC) CLI from the matching release checkout.

Choose a target for your cluster.

| Target                               | Infrastructure                                                                                     | Verification                                                                                 |
| ------------------------------------ | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [AWS EKS](targets/aws-eks/)          | Dedicated VPC, private node pools, EKS add-ons, operator access, and encrypted storage preparation | Earlier disposable OCE install and upgrade. Model turns and full teardown remain unverified. |
| [Google Cloud GKE](targets/gcp-gke/) | Preview GKE Standard cluster and node pools in an existing network                                 | Local mock plans only                                                                        |
| [Azure AKS](targets/azure-aks/)      | Preview private AKS cluster and node pools                                                         | Local mock plans only                                                                        |

Each directory is a Terraform root with its own provider lockfile. Follow the target guide to configure credentials and fill in `terraform.tfvars`. For EKS, plan from the repository root with the following commands.

```bash
terraform -chdir=targets/aws-eks init -lockfile=readonly
terraform -chdir=targets/aws-eks plan -out=foundation.tfplan
```

Review the saved plan and its costs before applying it. GKE and AKS require `enable_preview = true` to plan their preview resources.

Keep a separate protected Terraform state and backend key for each target and installation. Use the original state to plan upgrades or destruction. Before destroying a target, remove OCE workloads and reconcile databases, load balancers, retained volumes, and backups with their owners.

## Configure OCE placement

Use the target directory and Terraform workspace of the cluster you checked.
Prepare an OCE site JSON file with `controlPlane` and `runtime` objects. Set the
current Kubernetes API CIDRs and other site settings. Omit example placement
values you don't intend to use.

From this repository's root, run the helper with Python 3 and Terraform on your
path. Choose a new file in a private directory you own.

```bash
./scripts/prepare-profile --target-dir targets/aws-eks \
  --input /secure/occ/site.json \
  --output /secure/occ/profile-input.json
```

The helper reads `terraform output -json oce_placement` for the selected
target. It fills in `controlPlane.nodeSelector`, `runtime.nodeSelector`,
`runtime.gatewayNodeSelector`, and `runtime.gatewayStorageClassName`. It
preserves extra labels and stops if existing values conflict. The site file
stays unchanged and outside Terraform state. The output has mode `0600`.

Confirm that the cluster has the selected StorageClass and nodes matching
**all** the labels. Then use the new file as `--input` for the
[OCE installation profile renderer](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/installation-profiles.md)
in your selected OCE release.

## Add a target

To add a target, create an independent root under `targets/` with its provider lockfile, inputs, outputs, operator guide, and credential-free plan tests. Register it in the explicit CI target matrix.

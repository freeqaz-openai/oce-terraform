# Terraform targets for OpenClaw Enterprise

Prepare cloud infrastructure for OpenClaw Enterprise (OCE), then install OCE with the OpenClaw Control Plane (OCC) CLI from the matching release checkout.

Choose a target for your cluster.

| Target                               | Infrastructure                                                                                     | Verification                                                                   |
| ------------------------------------ | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| [AWS EKS](targets/aws-eks/)          | Dedicated VPC, private node pools, EKS add-ons, operator access, and encrypted storage preparation | AWS read-only plan, local Terraform and handoff tests; live deployment pending |
| [Google Cloud GKE](targets/gcp-gke/) | Preview GKE Standard cluster and node pools in an existing network                                 | Local mock-plan tests; cloud deployment and OCE pending                        |
| [Azure AKS](targets/azure-aks/)      | Preview private AKS cluster and node pools                                                         | Local mock-plan tests; cloud deployment and OCE pending                        |

Each directory is a Terraform root with its own provider lockfile. Follow the target guide to configure credentials and fill in `terraform.tfvars`. For EKS, plan from the repository root with the following commands.

```bash
terraform -chdir=targets/aws-eks init -lockfile=readonly
terraform -chdir=targets/aws-eks plan -out=foundation.tfplan
```

Review the saved plan and its costs before applying it. GKE and AKS require `enable_preview = true` to plan their preview resources.

Keep a separate protected Terraform state and backend key for each target and installation. Use the original state to plan upgrades or destruction. Before destroying a target, remove OCE workloads and reconcile databases, load balancers, retained volumes, and backups with their owners.

## Configure OCE placement

After applying your target, print its placement settings from the same target directory.

```bash
terraform output -json oce_placement
```

Copy the printed fields into the corresponding `controlPlane` and `runtime`
objects in `profile-input.json`. Keep the other fields in each object. Use the
target guide to verify the selected node pools and Gateway StorageClass before
rendering the installation profile.
[OCE installation profiles](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/installation-profiles.md).

## Add a target

To add a target, create an independent root under `targets/` with its provider lockfile, inputs, outputs, operator guide, and credential-free plan tests. Register it in the explicit CI target matrix.

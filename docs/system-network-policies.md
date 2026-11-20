# Prepare the system add-ons for strict NetworkPolicy

Apply this reference's VPC CNI add-on in standard mode first. Prepare and test the
system policies while the cluster is dedicated to setup. In strict mode, a newly
started Pod starts with traffic denied until its policy is ready. This applies to
CoreDNS too. [AWS strict-mode setup](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html)

This example targets the stock managed IPv4 CoreDNS and EBS CSI add-ons on Linux
nodes. CoreDNS needs the Kubernetes API and its upstream resolver. EBS CSI
needs the Kubernetes API and cluster DNS. The controller also needs regional
STS for its service account role and the EC2 API for EBS operations.
[CoreDNS configuration](https://kubernetes.io/docs/tasks/administer-cluster/dns-custom-nameservers/#coredns-configmap-options) ·
[EBS CSI controller and node requirements](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/v1.67.2/docs/install.md)

## Collect the destination addresses

Use the same private kubeconfig and context as the [cluster handoff](../README.md#2-select-the-applied-cluster).
Inspect the actual Pod templates and Corefile before applying the example.

```bash
kube() { kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" "$@"; }
kube -n kube-system get deployment coredns ebs-csi-controller -o yaml
kube -n kube-system get daemonset ebs-csi-node -o yaml
kube -n kube-system get configmap coredns -o jsonpath='{.data.Corefile}'
```

- Confirm the CoreDNS label `k8s-app=kube-dns`, and the CSI labels
  `app=ebs-csi-controller` and `app=ebs-csi-node`, on the Pod templates.
  Confirm all three use Pod networking. Check that `kube-dns` selects CoreDNS
  and exposes TCP and UDP port 53. The stock CSI driver can obtain node
  metadata from Kubernetes with the default IMDS hop limit. Verify CSI
  registration and a test volume before assuming it can operate with that path.
  [AWS CoreDNS selector](https://docs.aws.amazon.com/eks/latest/best-practices/network-security.html#network-policy) ·
  [CSI controller manifest](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/v1.67.2/deploy/kubernetes/base/controller.yaml) ·
  [CSI node manifest](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/v1.67.2/deploy/kubernetes/base/node.yaml)
- Confirm TCP 443 for both the API Service and its IPv4 backends. Check
  that the CoreDNS forwarding target is AmazonProvidedDNS. The policies
  include the VPC's primary IPv4 network address plus two and the
  link-local resolver at `169.254.169.253`.
  [AmazonProvidedDNS addresses](https://docs.aws.amazon.com/vpc/latest/userguide/AmazonDNS-concepts.html)
- Terraform creates an EC2 interface endpoint and a regional STS interface
  endpoint across both Availability Zones. It enables private DNS and allows
  HTTPS from the EKS cluster security group. Review both endpoints and their
  hourly and data processing charges in the plan. Verify that the CSI driver
  uses regional STS and that the regional EC2 and STS names resolve inside
  the VPC to the corresponding endpoint interfaces.
  [AWS service endpoints](https://docs.aws.amazon.com/eks/latest/userguide/private-clusters.html) ·
  [Regional STS for service accounts](https://docs.aws.amazon.com/eks/latest/userguide/configure-sts-endpoint.html)

## Render and review the policies

The EKS inventory command and policy renderer require the credential owner to
explicitly export `KUBECTL_KUBERC=false` in the operator shell. Set it only when
the owner permits credentialed reads without kubectl preferences.

From this Terraform checkout, use one of the following methods to collect the
API address inventory. Keep the same verified kubeconfig and context throughout.
Use the inventory command when the matching OCE checkout supports
`--inventory-eks`. Set `OCE_CHECKOUT` to that checkout's absolute path.

```bash
node "$OCE_CHECKOUT/scripts/diagnose-kubernetes-prerequisites.ts" \
  --inventory-eks "$EKS_CLUSTER_ARN" --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" \
  > "$RUN_DIR/eks-addresses.json"
```

If the selected OCE release predates that command, collect `API_CIDRS` with
its [EKS setup guide](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/eks-openshell-setup.md#refresh-api-network-policies-after-the-node-groups-join).
Then create the inventory for the renderer.

```bash
jq -n --arg context "$CONTEXT" --arg arn "$EKS_CLUSTER_ARN" --argjson api "$API_CIDRS" \
  '{context: $context, eks: {clusterArn: $arn}, apiCidrs: $api}' > "$RUN_DIR/eks-addresses.json"
```

The renderer verifies the inventory and kubeconfig against applied Terraform
state. It reads the live DNS Service address and uses the Terraform output for
the AWS endpoints and VPC resolver.

```bash
python3 examples/system-policies.py --inventory "$RUN_DIR/eks-addresses.json" \
  --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" \
  > "$RUN_DIR/system-network-policies.json"
```

Review the selectors and addresses. The example allows Pods to query CoreDNS on
port 53. Clients also need their own DNS egress rule where isolated. The policies
add API and DNS paths for their selected add-ons, plus HTTPS to the verified
EC2 and STS interface addresses for the CSI controller. Inspect existing
policies because allowed connections combine across matching policies.
[NetworkPolicy isolation and selectors](https://kubernetes.io/docs/concepts/services-networking/network-policies/#the-two-sorts-of-pod-isolation)

```bash
kube apply --dry-run=server -f "$RUN_DIR/system-network-policies.json"
kube apply -f "$RUN_DIR/system-network-policies.json"
```

Prepare any other controllers' DNS, API, webhook, and peer policies. In
`terraform.tfvars`, change the existing assignment to
`strict_network_policy = true` and keep it set for future plans. From this
Terraform checkout, review and apply the saved transition plan.

```bash
terraform plan -out=strict.tfplan
terraform apply strict.tfplan
kube -n kube-system rollout status daemonset/aws-node --timeout=5m
kube -n kube-system rollout status deployment/coredns --timeout=5m
kube -n kube-system rollout status deployment/ebs-csi-controller --timeout=5m
kube -n kube-system rollout status daemonset/ebs-csi-node --timeout=5m
```

Exercise an allowed and denied path from an owned Job. Check cluster and external
DNS, and provision and mount a disposable claim using `encrypted-gp3` to exercise
the CSI controller and node paths. Check real admission through each prerequisite
webhook. Observe these outcomes after Pod replacement under strict mode because
a Ready rollout alone cannot verify DNS, storage, admission or enforcement.
Refresh the input addresses after endpoint changes and before upgrades. Use
the original kubeconfig and Terraform state to review and correct a failed
transition before installing OCE.

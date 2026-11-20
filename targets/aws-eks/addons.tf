resource "aws_iam_openid_connect_provider" "cluster" {
  url            = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_issuer = trimprefix(aws_iam_openid_connect_provider.cluster.url, "https://")

  addon_roles = {
    vpc_cni = {
      name            = "vpc-cni"
      service_account = "aws-node"
      policy_path     = "AmazonEKS_CNI_Policy"
      tags            = {}
    }
    ebs_csi = {
      name            = "ebs-csi"
      service_account = "ebs-csi-controller-sa"
      policy_path     = "AmazonEBSCSIDriverEKSClusterScopedPolicy"
      tags            = { "eks-cluster-name" = var.cluster_name }
    }
  }
}

resource "aws_iam_role" "addon" {
  for_each = local.addon_roles

  name = "${var.cluster_name}-${each.value.name}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.cluster.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer}:aud" = "sts.amazonaws.com"
          "${local.oidc_issuer}:sub" = "system:serviceaccount:kube-system:${each.value.service_account}"
        }
      }
    }]
  })
  tags = each.value.tags
}

resource "aws_iam_role_policy_attachment" "addon" {
  for_each = local.addon_roles

  role       = aws_iam_role.addon[each.key].name
  policy_arn = "${local.aws_policy}/${each.value.policy_path}"
}

data "aws_eks_addon_version" "selected" {
  for_each = toset(["vpc-cni", "aws-ebs-csi-driver", "kube-proxy", "coredns"])

  addon_name         = each.value
  kubernetes_version = var.kubernetes_version
  most_recent        = true
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "vpc-cni"
  addon_version            = data.aws_eks_addon_version.selected["vpc-cni"].version
  service_account_role_arn = aws_iam_role.addon["vpc_cni"].arn
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
    env = {
      NETWORK_POLICY_ENFORCING_MODE = var.strict_network_policy ? "strict" : "standard"
    }
  })

  depends_on = [aws_iam_role_policy_attachment.addon]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name  = aws_eks_cluster.this.name
  addon_name    = "kube-proxy"
  addon_version = data.aws_eks_addon_version.selected["kube-proxy"].version
}

resource "aws_eks_addon" "coredns" {
  cluster_name  = aws_eks_cluster.this.name
  addon_name    = "coredns"
  addon_version = data.aws_eks_addon_version.selected["coredns"].version

  depends_on = [aws_eks_node_group.this]
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  addon_version            = data.aws_eks_addon_version.selected["aws-ebs-csi-driver"].version
  service_account_role_arn = aws_iam_role.addon["ebs_csi"].arn

  lifecycle {
    precondition {
      condition = (
        tonumber(split(".", trimprefix(data.aws_eks_addon_version.selected["aws-ebs-csi-driver"].version, "v"))[0]) > 1 ||
        tonumber(split(".", data.aws_eks_addon_version.selected["aws-ebs-csi-driver"].version)[1]) >= 58
      )
      error_message = "The scoped EBS CSI IAM policy requires driver v1.58.0 or newer."
    }
  }

  depends_on = [aws_iam_role_policy_attachment.addon, aws_eks_addon.coredns]
}

locals {
  aws_policy = "arn:${data.aws_partition.current.partition}:iam::aws:policy"

  # Terraform has no IPv4 overlap predicate. Compare the inclusive IP ranges.
  vpc_range = [
    for host in [0, -1] : sum([
      for index, octet in split(".", cidrhost(var.vpc_cidr, host)) :
      tonumber(octet) * pow(256, 3 - index)
    ])
  ]
  service_range = [
    for host in [0, -1] : sum([
      for index, octet in split(".", cidrhost(var.service_ipv4_cidr, host)) :
      tonumber(octet) * pow(256, 3 - index)
    ])
  ]
}

resource "aws_iam_role" "cluster" {
  name = "${var.cluster_name}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "${local.aws_policy}/AmazonEKSClusterPolicy"
}

resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${var.cluster_name}/cluster"
  retention_in_days = 30
}

resource "aws_eks_cluster" "this" {
  name                          = var.cluster_name
  role_arn                      = aws_iam_role.cluster.arn
  version                       = var.kubernetes_version
  bootstrap_self_managed_addons = false
  enabled_cluster_log_types     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  kubernetes_network_config {
    ip_family         = "ipv4"
    service_ipv4_cidr = var.service_ipv4_cidr
  }

  upgrade_policy {
    support_type = "STANDARD"
  }

  vpc_config {
    subnet_ids              = [for subnet in aws_subnet.private : subnet.id]
    endpoint_private_access = true
    endpoint_public_access  = length(var.api_public_access_cidrs) > 0
    public_access_cidrs     = length(var.api_public_access_cidrs) > 0 ? var.api_public_access_cidrs : null
  }

  lifecycle {
    precondition {
      condition     = local.vpc_range[1] < local.service_range[0] || local.service_range[1] < local.vpc_range[0]
      error_message = "The Kubernetes Service CIDR must not overlap the VPC CIDR."
    }
    precondition {
      condition     = alltrue([for zone in var.availability_zones : startswith(zone, var.aws_region)])
      error_message = "Both availability zones must belong to the selected region."
    }
  }

  depends_on = [aws_iam_role_policy_attachment.cluster, aws_cloudwatch_log_group.cluster]
}

resource "aws_vpc_security_group_ingress_rule" "operator" {
  for_each = var.api_private_access_cidrs

  description       = "Kubernetes API from an approved routed IPv4 network"
  security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_eks_access_entry" "operator" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.operator_role_arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "operator" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_eks_access_entry.operator.principal_arn
  policy_arn    = "arn:${data.aws_partition.current.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

resource "aws_iam_role" "node" {
  name = "${var.cluster_name}-eks-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly"])

  role       = aws_iam_role.node.name
  policy_arn = "${local.aws_policy}/${each.value}"
}

resource "aws_launch_template" "node" {
  name = "${var.cluster_name}-eks-node"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      encrypted             = true
      delete_on_termination = true
      volume_type           = "gp3"
      volume_size           = 80
    }
  }
}

resource "aws_eks_node_group" "this" {
  for_each = var.node_pools

  cluster_name    = aws_eks_cluster.this.name
  node_group_name = each.key
  version         = aws_eks_cluster.this.version
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [for subnet in aws_subnet.private : subnet.id]
  ami_type        = "AL2023_x86_64_STANDARD"
  instance_types  = each.value.instance_types
  capacity_type   = "ON_DEMAND"
  labels          = { "oce-role" = each.key }

  launch_template {
    id      = aws_launch_template.node.id
    version = tostring(aws_launch_template.node.latest_version)
  }

  scaling_config {
    min_size     = each.value.min_size
    desired_size = each.value.desired_size
    max_size     = each.value.max_size
  }

  update_config {
    max_unavailable = 1
  }

  depends_on = [
    aws_eks_addon.vpc_cni,
    aws_eks_addon.kube_proxy,
    aws_iam_role_policy_attachment.node,
    aws_route.private_internet,
    aws_route_table_association.private,
  ]
}

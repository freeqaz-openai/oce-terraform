mock_provider "aws" {
  override_during = plan

  mock_data "aws_partition" {
    defaults = {
      partition  = "aws"
      dns_suffix = "amazonaws.com"
    }
  }

  mock_resource "aws_eks_cluster" {
    defaults = {
      arn                   = "arn:aws:eks:us-east-2:123456789012:cluster/oce-evaluation"
      endpoint              = "https://example.eks.us-east-2.amazonaws.com"
      certificate_authority = [{ data = "c3ludGhldGljIHB1YmxpYyBDQSBkYXRh" }]
      identity = [{
        oidc = [{ issuer = "https://oidc.eks.us-east-2.amazonaws.com/id/example" }]
      }]
    }
  }

  mock_data "aws_eks_addon_version" {
    defaults = {
      version = "v1.67.2-eksbuild.1"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

# Distinct provider-assigned IDs make plan-time wiring observable without AWS.
override_resource {
  target          = aws_subnet.private["us-east-2a"]
  override_during = plan
  values          = { id = "subnet-00000000000000001" }
}

override_resource {
  target          = aws_subnet.private["us-east-2b"]
  override_during = plan
  values          = { id = "subnet-00000000000000002" }
}

override_resource {
  target          = aws_nat_gateway.this["us-east-2a"]
  override_during = plan
  values          = { id = "nat-00000000000000001" }
}

override_resource {
  target          = aws_nat_gateway.this["us-east-2b"]
  override_during = plan
  values          = { id = "nat-00000000000000002" }
}

override_resource {
  target          = aws_route_table.private["us-east-2a"]
  override_during = plan
  values          = { id = "rtb-00000000000000001" }
}

override_resource {
  target          = aws_route_table.private["us-east-2b"]
  override_during = plan
  values          = { id = "rtb-00000000000000002" }
}

override_resource {
  target          = aws_iam_role.addon["ebs_csi"]
  override_during = plan
  values          = { arn = "arn:aws:iam::123456789012:role/oce-evaluation-ebs-csi" }
}

variables {
  aws_region         = "us-east-2"
  cluster_name       = "oce-evaluation"
  availability_zones = ["us-east-2a", "us-east-2b"]
  operator_role_arn  = "arn:aws:iam::123456789012:role/OceOperator"
}

run "private_cluster" {
  command = plan

  # API access is private unless the operator opts in to a bounded public path.
  assert {
    condition = (
      one(aws_eks_cluster.this.vpc_config).endpoint_private_access &&
      !one(aws_eks_cluster.this.vpc_config).endpoint_public_access &&
      length(aws_vpc_security_group_ingress_rule.operator) == 0 &&
      toset(one(aws_eks_cluster.this.vpc_config).subnet_ids) == toset([for subnet in aws_subnet.private : subnet.id])
    )
    error_message = "The default EKS API must be private and use only the private subnets."
  }

  # Terraform's provisioning identity must not inherit Kubernetes admin access.
  assert {
    condition = (
      one(aws_eks_cluster.this.access_config).authentication_mode == "API" &&
      !one(aws_eks_cluster.this.access_config).bootstrap_cluster_creator_admin_permissions &&
      aws_eks_access_entry.operator.principal_arn == var.operator_role_arn &&
      aws_eks_access_policy_association.operator.principal_arn == var.operator_role_arn
    )
    error_message = "Grant cluster access to the named operator role without making the cluster creator an administrator."
  }

  # The kubeconfig helper needs the cluster identity and CA from applied state.
  assert {
    condition = (
      output.oce_cluster.region == "us-east-2" &&
      output.oce_cluster.name == "oce-evaluation" &&
      output.oce_cluster.arn == "arn:aws:eks:us-east-2:123456789012:cluster/oce-evaluation" &&
      output.oce_cluster.endpoint == "https://example.eks.us-east-2.amazonaws.com" &&
      output.oce_cluster.certificate_authority_data == "c3ludGhldGljIHB1YmxpYyBDQSBkYXRh"
    )
    error_message = "The handoff must identify the applied cluster and preserve its TLS CA."
  }

  # Workers must get outbound access through the NAT in their subnet's zone.
  assert {
    condition = length(aws_subnet.private) == 2 && alltrue([
      for zone, subnet in aws_subnet.private :
      !subnet.map_public_ip_on_launch &&
      aws_route.private_internet[zone].destination_cidr_block == "0.0.0.0/0" &&
      aws_route.private_internet[zone].nat_gateway_id == aws_nat_gateway.this[zone].id &&
      aws_route_table_association.private[zone].subnet_id == subnet.id &&
      aws_route_table_association.private[zone].route_table_id == aws_route.private_internet[zone].route_table_id
    ])
    error_message = "Keep both worker subnets private and route each through its zone's NAT."
  }

  # These labels are an OCE scheduling contract. Both pools need private connectivity.
  assert {
    condition = toset(keys(aws_eks_node_group.this)) == toset(["control", "agents"]) && alltrue([
      for role, group in aws_eks_node_group.this :
      group.labels["oce-role"] == role &&
      group.ami_type == "AL2023_x86_64_STANDARD" &&
      toset(group.subnet_ids) == toset([for subnet in aws_subnet.private : subnet.id])
    ])
    error_message = "Keep separate, AMD64 control and Agent node pools with the OCE labels and private subnets."
  }

  assert {
    condition = (
      one(aws_launch_template.node.metadata_options).http_tokens == "required" &&
      one(aws_launch_template.node.metadata_options).http_put_response_hop_limit == 1 &&
      one(one(aws_launch_template.node.block_device_mappings).ebs).encrypted
    )
    error_message = "Worker nodes need encrypted root volumes and IMDSv2 with a one-hop response limit."
  }

  # CoreDNS and CSI must start before switching from standard to strict enforcement.
  assert {
    condition = (
      tostring(jsondecode(aws_eks_addon.vpc_cni.configuration_values).enableNetworkPolicy) == "true" &&
      jsondecode(aws_eks_addon.vpc_cni.configuration_values).env.NETWORK_POLICY_ENFORCING_MODE == "standard"
    )
    error_message = "The bootstrap plan must enable VPC CNI NetworkPolicy in standard mode."
  }

  assert {
    condition = (
      aws_eks_addon.ebs_csi.service_account_role_arn == aws_iam_role.addon["ebs_csi"].arn &&
      aws_iam_role.addon["ebs_csi"].tags["eks-cluster-name"] == var.cluster_name &&
      aws_iam_role_policy_attachment.addon["ebs_csi"].policy_arn == "arn:aws:iam::aws:policy/AmazonEBSCSIDriverEKSClusterScopedPolicy"
    )
    error_message = "EBS CSI must use a cluster-scoped role instead of inheriting the node role."
  }
}

run "restrict_operator_access_and_enable_strict_network_policy" {
  command = plan

  variables {
    api_public_access_cidrs  = ["203.0.113.8/32", "198.51.100.16/32"]
    api_private_access_cidrs = ["192.168.6.0/24"]
    strict_network_policy    = true
  }

  assert {
    condition = (
      one(aws_eks_cluster.this.vpc_config).endpoint_private_access &&
      one(aws_eks_cluster.this.vpc_config).endpoint_public_access &&
      toset(one(aws_eks_cluster.this.vpc_config).public_access_cidrs) == toset(["203.0.113.8/32", "198.51.100.16/32"])
    )
    error_message = "Opt-in access must preserve the private endpoint and use exactly the reviewed operator CIDRs."
  }

  assert {
    condition = (
      toset(keys(aws_vpc_security_group_ingress_rule.operator)) == toset(["192.168.6.0/24"]) &&
      one(values(aws_vpc_security_group_ingress_rule.operator)).security_group_id == one(aws_eks_cluster.this.vpc_config).cluster_security_group_id &&
      one(values(aws_vpc_security_group_ingress_rule.operator)).cidr_ipv4 == "192.168.6.0/24" &&
      one(values(aws_vpc_security_group_ingress_rule.operator)).ip_protocol == "tcp" &&
      one(values(aws_vpc_security_group_ingress_rule.operator)).from_port == 443 &&
      one(values(aws_vpc_security_group_ingress_rule.operator)).to_port == 443
    )
    error_message = "The private route must grant the approved operator network access to the Kubernetes API port only."
  }

  assert {
    condition = (
      tostring(jsondecode(aws_eks_addon.vpc_cni.configuration_values).enableNetworkPolicy) == "true" &&
      jsondecode(aws_eks_addon.vpc_cni.configuration_values).env.NETWORK_POLICY_ENFORCING_MODE == "strict"
    )
    error_message = "The second-stage plan must enable strict NetworkPolicy before deploying OCE."
  }
}

run "refuse_overlapping_service_cidr" {
  command = plan

  variables {
    vpc_cidr          = "10.42.0.0/16"
    service_ipv4_cidr = "10.32.0.0/12"
  }

  expect_failures = [aws_eks_cluster.this]
}

run "refuse_unrestricted_public_api" {
  command = plan

  variables {
    api_public_access_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.api_public_access_cidrs]
}

run "refuse_unsupported_kubernetes" {
  command = plan

  variables {
    kubernetes_version = "1.34"
  }

  expect_failures = [var.kubernetes_version]
}

run "refuse_empty_agent_pool" {
  command = plan

  variables {
    node_pools = {
      control = {
        instance_types = ["m6i.xlarge"]
        min_size       = 1
        desired_size   = 1
        max_size       = 2
      }
      agents = {
        instance_types = ["m6i.2xlarge"]
        min_size       = 0
        desired_size   = 0
        max_size       = 1
      }
    }
  }

  expect_failures = [var.node_pools]
}

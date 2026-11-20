mock_provider "aws" {
  override_during = plan

  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/OceOperator" }
  }

  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-2a", "us-east-2b"] }
  }

  mock_data "aws_ec2_instance_types" {
    defaults = { instance_types = ["m6i.xlarge", "m7i.xlarge", "m6i.2xlarge", "m7i.2xlarge"] }
  }

  mock_data "aws_ec2_instance_type_offerings" {
    defaults = { instance_types = ["m6i.xlarge", "m6i.2xlarge"] }
  }

  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }

  mock_data "aws_eks_addon_version" {
    defaults = { version = "v1.67.2-eksbuild.1" }
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
}

override_resource {
  target          = aws_subnet.endpoints["us-east-2a"]
  override_during = plan
  values          = { id = "subnet-00000000000000001" }
}

override_resource {
  target          = aws_subnet.endpoints["us-east-2b"]
  override_during = plan
  values          = { id = "subnet-00000000000000002" }
}

override_resource {
  target          = aws_security_group.service_endpoints
  override_during = plan
  values          = { id = "sg-00000000000000001" }
}

variables {
  aws_region         = "us-east-2"
  cluster_name       = "oce-evaluation"
  availability_zones = ["us-east-2a", "us-east-2b"]
  operator_role_arn  = "arn:aws:iam::123456789012:role/OceOperator"
}

run "private_system_service_endpoints" {
  command = plan

  # Only callers with the EKS group can establish new connections to the interface.
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.service_endpoints.referenced_security_group_id
      == one(aws_eks_cluster.this.vpc_config).cluster_security_group_id &&
      aws_vpc_security_group_ingress_rule.service_endpoints.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.service_endpoints.ip_protocol == "tcp" &&
      aws_vpc_security_group_ingress_rule.service_endpoints.from_port == 443 &&
      aws_vpc_security_group_ingress_rule.service_endpoints.to_port == 443
    )
    error_message = "Only HTTPS from the EKS cluster security group should reach the AWS service endpoints."
  }

  assert {
    condition = alltrue([
      for name, endpoint in aws_vpc_endpoint.service :
      endpoint.service_name == "com.amazonaws.us-east-2.${name}" &&
      endpoint.private_dns_enabled && endpoint.vpc_endpoint_type == "Interface" &&
      endpoint.ip_address_type == "ipv4" &&
      endpoint.security_group_ids == toset([aws_security_group.service_endpoints.id]) &&
      toset([for config in endpoint.subnet_configuration : config.subnet_id]) ==
      toset([for subnet in aws_subnet.endpoints : subnet.id])
    ])
    error_message = "Private DNS must resolve both services to an interface in each dedicated endpoint subnet."
  }

  # The output follows the configured addresses and identifies the exact cluster.
  assert {
    condition = (
      output.system_network.cluster_arn == output.oce_cluster.arn &&
      output.system_network.endpoint == output.oce_cluster.endpoint &&
      output.system_network.ca_data == output.oce_cluster.certificate_authority_data &&
      toset(output.system_network.resolver_ipv4) == toset(["10.42.0.2", "169.254.169.253"]) &&
      toset(output.system_network.ec2_endpoint_ipv4) == toset(["10.42.255.229", "10.42.255.245"]) &&
      toset(output.system_network.sts_endpoint_ipv4) == toset(["10.42.255.230", "10.42.255.246"])
    )
    error_message = "The renderer must get one independent address per service per zone and the cluster and resolver from applied state."
  }
}

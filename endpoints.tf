locals {
  # The last two /28s are outside the four larger networking subnets. Keep
  # endpoint IPs here so worker ENIs cannot claim them during bootstrap.
  endpoint_subnets = {
    for zone, index in local.zone_indices : zone => cidrsubnet(var.vpc_cidr, 12, 4094 + index)
  }
  endpoint_ipv4 = {
    for service, host in { ec2 = 5, sts = 6 } : service => {
      for zone, cidr in local.endpoint_subnets : zone => cidrhost(cidr, host)
    }
  }
}

resource "aws_subnet" "endpoints" {
  for_each = local.endpoint_subnets

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false

  tags = { Name = "${var.cluster_name}-endpoints-${each.key}" }

  lifecycle {
    precondition {
      condition = (
        startswith(var.vpc_cidr, "10.") || startswith(var.vpc_cidr, "192.168.") ||
        can(regex("^172\\.(1[6-9]|2[0-9]|3[01])\\.", var.vpc_cidr))
      )
      error_message = "Use an RFC1918 VPC CIDR for the EKS system policy inputs."
    }
  }
}

resource "aws_security_group" "service_endpoints" {
  name        = "${var.cluster_name}-service-endpoints"
  description = "HTTPS from EKS nodes to EC2 and regional STS"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "service_endpoints" {
  security_group_id            = aws_security_group.service_endpoints.id
  referenced_security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_endpoint" "service" {
  for_each = local.endpoint_ipv4

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  ip_address_type     = "ipv4"
  private_dns_enabled = true
  subnet_ids          = [for subnet in aws_subnet.endpoints : subnet.id]
  security_group_ids  = [aws_security_group.service_endpoints.id]

  dynamic "subnet_configuration" {
    for_each = each.value

    content {
      subnet_id = aws_subnet.endpoints[subnet_configuration.key].id
      ipv4      = subnet_configuration.value
    }
  }
}

output "system_network" {
  description = "Applied EKS identity and private destinations for the system NetworkPolicy renderer."
  value = {
    cluster_arn       = aws_eks_cluster.this.arn
    endpoint          = aws_eks_cluster.this.endpoint
    ca_data           = aws_eks_cluster.this.certificate_authority[0].data
    resolver_ipv4     = [cidrhost(aws_vpc.this.cidr_block, 2), "169.254.169.253"]
    ec2_endpoint_ipv4 = sort([for subnet in aws_vpc_endpoint.service["ec2"].subnet_configuration : subnet.ipv4])
    sts_endpoint_ipv4 = sort([for subnet in aws_vpc_endpoint.service["sts"].subnet_configuration : subnet.ipv4])
  }
}

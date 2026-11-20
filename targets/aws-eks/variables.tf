variable "aws_region" {
  description = "AWS region for the VPC and EKS cluster."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region)) && !startswith(var.aws_region, "cn-")
    error_message = "Choose an AWS commercial region, for example us-east-2."
  }
}

variable "cluster_name" {
  description = "Short lowercase EKS cluster name, unique in this account and region."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?$", var.cluster_name))
    error_message = "Use a 1–48 character lowercase DNS label."
  }
}

variable "availability_zones" {
  description = "Two standard availability zones in the cluster region. Select these before creating the cluster."
  type        = set(string)

  validation {
    condition = length(var.availability_zones) == 2 && alltrue([
      for zone in var.availability_zones : can(regex("^[a-z]{2}-[a-z]+-[0-9]+[a-z]$", zone))
    ])
    error_message = "Select exactly two standard availability zone names."
  }
}

variable "single_nat_gateway_zone" {
  description = "Optional selected availability zone for a single NAT gateway shared by both private subnets. The default creates one NAT gateway per zone."
  type        = string
  default     = null

  validation {
    condition     = var.single_nat_gateway_zone == null ? true : contains(var.availability_zones, var.single_nat_gateway_zone)
    error_message = "The single NAT gateway zone must be one of the cluster availability zones."
  }
}

variable "operator_role_arn" {
  description = "Existing IAM role in the cluster account for Kubernetes installation and administration."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]+$", var.operator_role_arn))
    error_message = "Provide an IAM role ARN, such as arn:aws:iam::123456789012:role/OceOperator."
  }
}

variable "kubernetes_version" {
  description = "EKS Kubernetes minor version. Verify the selected OCE release and regional EKS support before changing it."
  type        = string
  default     = "1.36"

  validation {
    condition     = can(regex("^1\\.[0-9]{2}$", var.kubernetes_version)) && try(tonumber(split(".", var.kubernetes_version)[1]) >= 35, false)
    error_message = "Select an EKS Kubernetes version at least 1.35, with no patch suffix."
  }
}

variable "vpc_cidr" {
  description = "IPv4 /16 network for the cluster. Keep this distinct from connected networks and the Kubernetes Service CIDR."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition = try(
      cidrnetmask(var.vpc_cidr) == "255.255.0.0" &&
      split("/", var.vpc_cidr)[0] == cidrhost(var.vpc_cidr, 0),
      false
    )
    error_message = "Use an IPv4 /16 network address with zero host bits."
  }
}

variable "service_ipv4_cidr" {
  description = "Kubernetes IPv4 Service CIDR. Select a non-overlapping RFC1918 range before creating the cluster."
  type        = string
  default     = "172.20.0.0/16"

  validation {
    condition = try(
      cidrnetmask(var.service_ipv4_cidr) != "" &&
      split("/", var.service_ipv4_cidr)[0] == cidrhost(var.service_ipv4_cidr, 0) &&
      tonumber(split("/", var.service_ipv4_cidr)[1]) >= 12 &&
      tonumber(split("/", var.service_ipv4_cidr)[1]) <= 24 &&
      (
        startswith(var.service_ipv4_cidr, "10.") ||
        (startswith(var.service_ipv4_cidr, "172.") && tonumber(split(".", var.service_ipv4_cidr)[1]) >= 16 && tonumber(split(".", var.service_ipv4_cidr)[1]) <= 31) ||
        (startswith(var.service_ipv4_cidr, "192.168.") && tonumber(split("/", var.service_ipv4_cidr)[1]) >= 16)
      ),
      false
    )
    error_message = "Use an IPv4 RFC1918 network CIDR between /12 and /24 with zero host bits."
  }
}

variable "api_public_access_cidrs" {
  description = "Reviewed, stable operator egress IPv4 CIDRs for the public EKS API endpoint. Empty keeps the API private-only."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.api_public_access_cidrs : try(
        cidrnetmask(cidr) != "" &&
        tonumber(split("/", cidr)[1]) >= 24 &&
        split("/", cidr)[0] == cidrhost(cidr, 0),
        false
      )
    ])
    error_message = "Use IPv4 operator CIDRs of /24 or narrower with zero host bits."
  }
}

variable "api_private_access_cidrs" {
  description = "IPv4 CIDRs for operators with routed access to the private EKS API. Empty grants no extra ingress."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.api_private_access_cidrs : try(
        cidrnetmask(cidr) != "" &&
        tonumber(split("/", cidr)[1]) > 0 &&
        split("/", cidr)[0] == cidrhost(cidr, 0),
        false
      )
    ])
    error_message = "Specify routed IPv4 networks with zero host bits and no default route."
  }
}

variable "node_pools" {
  description = "AMD64 EC2 instance options and scaling bounds for the control and Agent pools."
  type = object({
    control = object({
      instance_types = list(string)
      min_size       = number
      desired_size   = number
      max_size       = number
    })
    agents = object({
      instance_types = list(string)
      min_size       = number
      desired_size   = number
      max_size       = number
    })
  })
  default = {
    control = {
      instance_types = ["m7i.xlarge", "m6i.xlarge"]
      min_size       = 2
      desired_size   = 2
      max_size       = 4
    }
    agents = {
      instance_types = ["m7i.2xlarge", "m6i.2xlarge"]
      min_size       = 2
      desired_size   = 2
      max_size       = 4
    }
  }

  validation {
    condition = alltrue([
      for pool in var.node_pools : (
        length(pool.instance_types) > 0 &&
        pool.min_size >= 1 &&
        pool.min_size <= pool.desired_size &&
        pool.desired_size <= pool.max_size &&
        alltrue([for size in [pool.min_size, pool.desired_size, pool.max_size] : floor(size) == size])
      )
    ])
    error_message = "Each pool needs an instance type and integer counts with 1 <= min_size <= desired_size <= max_size."
  }
}

variable "strict_network_policy" {
  description = "Deny pod traffic while VPC CNI policies initialize. Enable after applying system service policies and before deploying OCE."
  type        = bool
  default     = false
}

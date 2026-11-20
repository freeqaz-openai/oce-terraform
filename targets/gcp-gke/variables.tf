variable "enable_preview" {
  description = "Opt in to the unqualified GKE target."
  type        = bool
  default     = false

  validation {
    condition     = var.enable_preview
    error_message = "Set enable_preview = true to plan the GKE preview."
  }
}

variable "project_id" {
  description = "Dedicated Google Cloud project with billing and the Compute Engine and GKE APIs enabled."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Provide a valid Google Cloud project ID."
  }
}

variable "region" {
  description = "Region of the existing subnet and Cloud NAT."
  type        = string
  nullable    = false
}

variable "zone" {
  description = "Zone for the GKE control plane and both worker pools."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region)) && can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", var.zone)) && startswith(var.zone, "${var.region}-")
    error_message = "The zone must belong to the selected region."
  }
}

variable "cluster_name" {
  description = "Name of the dedicated GKE cluster."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,38}[a-z0-9]$", var.cluster_name))
    error_message = "Use a GKE cluster name of 2 to 40 lowercase letters, numbers, or hyphens, beginning with a letter and ending with a letter or number."
  }
}

variable "subnet_name" {
  description = "Existing subnet in the project and region, with Private Google Access enabled."
  type        = string
  nullable    = false
}

variable "pod_range_name" {
  description = "Existing IPv4 secondary subnet range for Pods."
  type        = string
  nullable    = false
}

variable "service_range_name" {
  description = "A separate existing IPv4 secondary subnet range for Services."
  type        = string
  nullable    = false

  validation {
    condition     = var.service_range_name != var.pod_range_name
    error_message = "Use distinct Pod and Service secondary ranges."
  }
}

variable "control_plane_cidr" {
  description = "Unused IPv4 /28 for the private control plane. Check connected networks for overlap."
  type        = string
  nullable    = false

  validation {
    condition     = can(cidrnetmask(var.control_plane_cidr)) && endswith(var.control_plane_cidr, "/28")
    error_message = "Provide an unused IPv4 /28 for the GKE control plane."
  }
}

variable "operator_cidrs" {
  description = "Routed operator IPv4 CIDRs permitted on the private Kubernetes API endpoint."
  type        = set(string)
  nullable    = false

  validation {
    condition = length(var.operator_cidrs) > 0 && alltrue([
      for cidr in var.operator_cidrs : try(can(cidrnetmask(cidr)) && tonumber(split("/", cidr)[1]) >= 16, false)
    ])
    error_message = "Provide at least one explicitly approved IPv4 CIDR with a prefix of /16 or narrower."
  }
}

variable "node_service_account" {
  description = "Existing least-privilege node service account in the selected project."
  type        = string
  nullable    = false

  validation {
    condition = (
      endswith(var.node_service_account, "@${var.project_id}.iam.gserviceaccount.com") &&
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@", var.node_service_account))
    )
    error_message = "Provide a user-managed node service account email from the selected project."
  }
}

variable "deletion_protection" {
  description = "Protect the GKE cluster against accidental destruction."
  type        = bool
  default     = true
}

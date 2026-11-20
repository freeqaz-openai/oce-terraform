variable "enable_preview" {
  description = "Acknowledge that this target has no live OCE qualification."
  type        = bool
  default     = false
  nullable    = false

  validation {
    condition     = var.enable_preview
    error_message = "AKS is a preview target. Set enable_preview = true after reviewing the AKS target README."
  }
}

variable "subscription_id" {
  description = "Azure subscription ID for all resources."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "Enter the Azure subscription ID as a UUID."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID for Azure and Kubernetes authentication."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "Enter the Entra tenant ID as a UUID."
  }
}

variable "operator_group_object_id" {
  description = "Object ID of the existing Entra group authorized to administer this AKS cluster."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", var.operator_group_object_id))
    error_message = "Enter the Entra group object ID as a UUID."
  }
}

variable "location" {
  description = "Azure region where the selected VM SKU is available."
  type        = string
  nullable    = false
}

variable "cluster_name" {
  description = "Separate name for this AKS cluster and its resource groups."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,43}[a-z0-9]$", var.cluster_name))
    error_message = "Use a 2–45 character lowercase cluster name beginning with a letter and ending with a letter or digit."
  }
}

variable "kubernetes_version" {
  description = "AKS Kubernetes minor version (1.35 or newer) available in the region."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^1\\.(3[5-9]|[4-9][0-9])$", var.kubernetes_version))
    error_message = "Choose an available AKS Kubernetes minor release at or above 1.35, such as 1.36."
  }
}

variable "vm_size" {
  description = "Linux amd64 VM SKU with sufficient capacity for each OCE node pool."
  type        = string
  default     = "Standard_D4s_v5"
  nullable    = false
}

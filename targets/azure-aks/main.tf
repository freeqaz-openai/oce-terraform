resource "azurerm_resource_group" "aks" {
  name     = "${var.cluster_name}-rg"
  location = var.location

  lifecycle {
    precondition {
      condition     = var.enable_preview
      error_message = "AKS is a preview target. Set enable_preview = true after reviewing the AKS target README."
    }
  }
}

resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.cluster_name
  location            = azurerm_resource_group.aks.location
  resource_group_name = azurerm_resource_group.aks.name
  node_resource_group = "${var.cluster_name}-nodes"
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Standard"

  private_cluster_enabled             = true
  private_cluster_public_fqdn_enabled = false
  private_dns_zone_id                 = "System"
  local_account_disabled              = true
  role_based_access_control_enabled   = true
  run_command_enabled                 = false

  node_provisioning_profile {
    mode = "Manual"
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    tenant_id          = var.tenant_id
    azure_rbac_enabled = true
  }

  default_node_pool {
    name                         = "control"
    vm_size                      = var.vm_size
    node_count                   = 2
    node_public_ip_enabled       = false
    only_critical_addons_enabled = false
    node_labels                  = { "oce-role" = "control" }
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    load_balancer_sku   = "standard"
    outbound_type       = "loadBalancer"
  }

  storage_profile {
    disk_driver_enabled = true
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "agents" {
  name                   = "agents"
  kubernetes_cluster_id  = azurerm_kubernetes_cluster.aks.id
  orchestrator_version   = var.kubernetes_version
  mode                   = "User"
  vm_size                = var.vm_size
  node_count             = 1
  node_public_ip_enabled = false
  node_labels            = { "oce-role" = "agents" }
}

resource "azurerm_role_assignment" "operator" {
  for_each = toset([
    "Azure Kubernetes Service Cluster User Role",
    "Azure Kubernetes Service RBAC Cluster Admin",
  ])

  scope                = azurerm_kubernetes_cluster.aks.id
  role_definition_name = each.key
  principal_id         = var.operator_group_object_id
  principal_type       = "Group"
}

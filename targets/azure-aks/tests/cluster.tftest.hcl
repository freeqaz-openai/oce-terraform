mock_provider "azurerm" {}

variables {
  subscription_id          = "00000000-0000-0000-0000-000000000001"
  tenant_id                = "00000000-0000-0000-0000-000000000002"
  operator_group_object_id = "00000000-0000-0000-0000-000000000003"
  location                 = "eastus2"
  cluster_name             = "oce-aks-example"
  kubernetes_version       = "1.36"
}

run "requires_preview_opt_in" {
  command = plan

  expect_failures = [var.enable_preview]
}

run "targeted_plan_requires_preview_opt_in" {
  command = plan

  plan_options {
    target = [azurerm_resource_group.aks]
  }

  expect_failures = [var.enable_preview]
}

run "plans_private_pools_and_policy" {
  command = plan

  variables {
    enable_preview = true
  }

  override_resource {
    target          = azurerm_kubernetes_cluster.aks
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/oce-aks-example-rg/providers/Microsoft.ContainerService/managedClusters/oce-aks-example"
    }
  }

  assert {
    condition = (
      azurerm_kubernetes_cluster.aks.private_cluster_enabled &&
      !azurerm_kubernetes_cluster.aks.private_cluster_public_fqdn_enabled &&
      azurerm_kubernetes_cluster.aks.local_account_disabled &&
      !azurerm_kubernetes_cluster.aks.run_command_enabled &&
      azurerm_kubernetes_cluster.aks.azure_active_directory_role_based_access_control[0].azure_rbac_enabled
    )
    error_message = "The API must be private and use Microsoft Entra and Azure RBAC."
  }

  assert {
    condition = (
      azurerm_kubernetes_cluster.aks.network_profile[0].network_plugin_mode == "overlay" &&
      azurerm_kubernetes_cluster.aks.network_profile[0].network_policy == "cilium" &&
      azurerm_kubernetes_cluster.aks.storage_profile[0].disk_driver_enabled &&
      azurerm_kubernetes_cluster.aks.default_node_pool[0].node_labels["oce-role"] == "control" &&
      !azurerm_kubernetes_cluster.aks.default_node_pool[0].node_public_ip_enabled &&
      azurerm_kubernetes_cluster_node_pool.agents.node_labels["oce-role"] == "agents" &&
      !azurerm_kubernetes_cluster_node_pool.agents.node_public_ip_enabled
    )
    error_message = "The preview needs Cilium policies, disk CSI, and distinct private control and Agent pools."
  }

  assert {
    condition = alltrue([
      for role in azurerm_role_assignment.operator :
      role.scope == azurerm_kubernetes_cluster.aks.id &&
      role.principal_id == var.operator_group_object_id &&
      role.principal_type == "Group"
      ]) && toset(keys(azurerm_role_assignment.operator)) == toset([
      "Azure Kubernetes Service Cluster User Role",
      "Azure Kubernetes Service RBAC Cluster Admin",
    ])
    error_message = "Only the selected Entra group should receive the user and admin roles at cluster scope."
  }

  assert {
    condition = output.oce_placement == {
      controlPlane = {
        nodeSelector = { "oce-role" = "control" }
      }
      runtime = {
        gatewayNodeSelector     = { "oce-role" = "control" }
        nodeSelector            = { "oce-role" = "agents" }
        gatewayStorageClassName = "managed-csi-premium"
      }
    }
    error_message = "The profile fragment must place each workload in its pool and select the target Gateway StorageClass."
  }
}

output "oce_cluster" {
  description = "AKS resource identity for the operator handoff."
  value = {
    id                  = azurerm_kubernetes_cluster.aks.id
    subscription_id     = var.subscription_id
    tenant_id           = var.tenant_id
    location            = azurerm_kubernetes_cluster.aks.location
    resource_group      = azurerm_resource_group.aks.name
    node_resource_group = azurerm_kubernetes_cluster.aks.node_resource_group
    name                = azurerm_kubernetes_cluster.aks.name
    private_fqdn        = azurerm_kubernetes_cluster.aks.private_fqdn
  }
}

locals {
  oce_control_selector = { "oce-role" = azurerm_kubernetes_cluster.aks.default_node_pool[0].node_labels["oce-role"] }
  oce_agent_selector   = { "oce-role" = azurerm_kubernetes_cluster_node_pool.agents.node_labels["oce-role"] }
}

output "oce_placement" {
  description = "Placement and Gateway storage fields for the OCE installation profile."
  value = {
    controlPlane = {
      nodeSelector = local.oce_control_selector
    }
    runtime = {
      gatewayNodeSelector     = local.oce_control_selector
      nodeSelector            = local.oce_agent_selector
      gatewayStorageClassName = "managed-csi-premium"
    }
  }
}

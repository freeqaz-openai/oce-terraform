output "oce_cluster" {
  description = "GKE cluster identity for the operator handoff."
  value = {
    project_id = var.project_id
    location   = var.zone
    name       = google_container_cluster.oce.name
  }
}

locals {
  oce_control_selector = { "oce-role" = google_container_node_pool.oce["control"].node_config[0].labels["oce-role"] }
  oce_agent_selector   = { "oce-role" = google_container_node_pool.oce["agents"].node_config[0].labels["oce-role"] }
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
      gatewayStorageClassName = yamldecode(file("${path.module}/examples/storage-class.yaml")).metadata.name
    }
  }
}

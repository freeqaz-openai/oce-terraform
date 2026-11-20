data "google_compute_subnetwork" "cluster" {
  name   = var.subnet_name
  region = var.region
}

resource "google_container_cluster" "oce" {
  name                     = var.cluster_name
  location                 = var.zone
  network                  = data.google_compute_subnetwork.cluster.network
  subnetwork               = data.google_compute_subnetwork.cluster.self_link
  networking_mode          = "VPC_NATIVE"
  datapath_provider        = "ADVANCED_DATAPATH"
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = var.deletion_protection

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pod_range_name
    services_secondary_range_name = var.service_range_name
  }

  private_cluster_config {
    enable_private_endpoint = true
    enable_private_nodes    = true
    master_ipv4_cidr_block  = var.control_plane_cidr
  }

  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled      = false
    private_endpoint_enforcement_enabled = true

    dynamic "cidr_blocks" {
      for_each = var.operator_cidrs
      content {
        cidr_block = cidr_blocks.value
      }
    }
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic    = false
      enable_k8s_tokens_via_dns = false
      enable_k8s_certs_via_dns  = false
    }
  }

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  addons_config {
    gce_persistent_disk_csi_driver_config {
      enabled = true
    }
  }

  # GKE can create a short-lived default pool while provisioning the cluster.
  node_config {
    service_account = var.node_service_account
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  lifecycle {
    precondition {
      condition     = var.enable_preview
      error_message = "Set enable_preview = true to plan the GKE preview."
    }

    precondition {
      condition = data.google_compute_subnetwork.cluster.private_ip_google_access && alltrue([
        for name in [var.pod_range_name, var.service_range_name] : contains(
          data.google_compute_subnetwork.cluster.secondary_ip_range[*].range_name,
          name,
        )
      ])
      error_message = "Enable Private Google Access on the selected subnet and provision both named secondary ranges."
    }
  }
}

resource "google_container_node_pool" "oce" {
  for_each = {
    control = "e2-standard-4"
    agents  = "e2-standard-8"
  }

  name       = each.key
  location   = var.zone
  cluster    = google_container_cluster.oce.name
  node_count = 1

  node_config {
    machine_type    = each.value
    image_type      = "UBUNTU_CONTAINERD"
    service_account = var.node_service_account
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { "oce-role" = each.key }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

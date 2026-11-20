mock_provider "google" {
  override_during = plan

  mock_data "google_compute_subnetwork" {
    defaults = {
      network                  = "https://www.googleapis.com/compute/v1/projects/example-oce-project/global/networks/oce-vpc"
      self_link                = "https://www.googleapis.com/compute/v1/projects/example-oce-project/regions/us-central1/subnetworks/oce-subnet"
      private_ip_google_access = true
      secondary_ip_range = [
        { range_name = "oce-pods", ip_cidr_range = "10.21.0.0/16" },
        { range_name = "oce-services", ip_cidr_range = "10.22.0.0/20" },
      ]
    }
  }
}

variables {
  project_id           = "example-oce-project"
  region               = "us-central1"
  zone                 = "us-central1-a"
  cluster_name         = "oce-preview"
  subnet_name          = "oce-subnet"
  pod_range_name       = "oce-pods"
  service_range_name   = "oce-services"
  control_plane_cidr   = "172.16.1.0/28"
  operator_cidrs       = ["10.20.30.40/32"]
  node_service_account = "oce-gke-node@example-oce-project.iam.gserviceaccount.com"
}

run "refuse_preview_without_opt_in" {
  command = plan

  expect_failures = [var.enable_preview]
}

run "refuse_targeted_preview_without_opt_in" {
  command = plan

  plan_options {
    target = [google_container_cluster.oce]
  }

  expect_failures = [var.enable_preview]
}

run "plan_private_cluster" {
  command = plan

  variables {
    enable_preview = true
  }

  assert {
    condition = (
      google_container_cluster.oce.location == "us-central1-a" &&
      google_container_cluster.oce.datapath_provider == "ADVANCED_DATAPATH" &&
      google_container_cluster.oce.private_cluster_config[0].enable_private_nodes &&
      google_container_cluster.oce.private_cluster_config[0].enable_private_endpoint &&
      google_container_cluster.oce.master_authorized_networks_config[0].private_endpoint_enforcement_enabled &&
      one(google_container_cluster.oce.master_authorized_networks_config[0].cidr_blocks).cidr_block == "10.20.30.40/32" &&
      !google_container_cluster.oce.control_plane_endpoints_config[0].dns_endpoint_config[0].allow_external_traffic
    )
    error_message = "The GKE cluster must keep nodes and the Kubernetes API private, with NetworkPolicy-capable networking."
  }

  assert {
    condition = (
      google_container_cluster.oce.addons_config[0].gce_persistent_disk_csi_driver_config[0].enabled &&
      google_container_cluster.oce.deletion_protection &&
      !google_container_cluster.oce.master_auth[0].client_certificate_config[0].issue_client_certificate &&
      toset(keys(google_container_node_pool.oce)) == toset(["control", "agents"]) &&
      alltrue([
        for role, pool in google_container_node_pool.oce :
        pool.node_config[0].labels["oce-role"] == role &&
        pool.node_config[0].service_account == var.node_service_account &&
        pool.node_config[0].workload_metadata_config[0].mode == "GKE_METADATA"
      ])
    )
    error_message = "Both node pools must carry the OCE role labels and use the selected identity, with persistent disk storage enabled."
  }

  assert {
    condition = output.oce_placement == {
      controlPlane = {
        nodeSelector = { "oce-role" = "control" }
      }
      runtime = {
        gatewayNodeSelector     = { "oce-role" = "control" }
        nodeSelector            = { "oce-role" = "agents" }
        gatewayStorageClassName = "oce-gke-rwo"
      }
    }
    error_message = "The profile fragment must place each workload in its pool and select the target Gateway StorageClass."
  }
}

run "refuse_open_api_allowlist" {
  command = plan

  variables {
    enable_preview = true
    operator_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.operator_cidrs]
}

run "refuse_missing_secondary_range" {
  command = plan

  variables {
    enable_preview     = true
    service_range_name = "no-such-range"
  }

  expect_failures = [google_container_cluster.oce]
}

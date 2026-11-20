output "oce_cluster" {
  description = "Cluster identity and public CA for scripts/prepare-kubeconfig. Contains no credentials."
  value = {
    region                     = var.aws_region
    name                       = aws_eks_cluster.this.name
    arn                        = aws_eks_cluster.this.arn
    endpoint                   = aws_eks_cluster.this.endpoint
    certificate_authority_data = aws_eks_cluster.this.certificate_authority[0].data
  }
}

locals {
  oce_control_selector = { "oce-role" = aws_eks_node_group.this["control"].labels["oce-role"] }
  oce_agent_selector   = { "oce-role" = aws_eks_node_group.this["agents"].labels["oce-role"] }
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
      gatewayStorageClassName = yamldecode(file("${path.module}/examples/encrypted-gp3.yaml")).metadata.name
    }
  }
}

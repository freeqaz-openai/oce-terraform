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

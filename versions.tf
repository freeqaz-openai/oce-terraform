terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [split(":", var.operator_role_arn)[4]]

  default_tags {
    tags = {
      "ManagedBy" = "Terraform"
      "Cluster"   = var.cluster_name
    }
  }
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

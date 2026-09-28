terraform {
  required_version = ">= 1.10"
  # Partial backend; the scripts pass bucket=pi-tfstate-<account_id> and key=<env>/foundation.tfstate
  backend "s3" {
    region       = "ca-central-1"
    use_lockfile = true # S3-native locking (Terraform >= 1.10)
    encrypt      = true
  }
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

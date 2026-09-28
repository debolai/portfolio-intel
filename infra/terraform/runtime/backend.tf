terraform {
  required_version = ">= 1.10"
  # Partial backend; the scripts pass bucket=pi-tfstate-<account_id> and key=<env>/runtime.tfstate
  backend "s3" {
    region       = "ca-central-1"
    use_lockfile = true
    encrypt      = true
  }
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

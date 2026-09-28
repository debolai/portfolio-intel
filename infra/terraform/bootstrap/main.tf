# Run once, with local state: the Terraform state bucket and the GitHub OIDC roles.
#   terraform -chdir=infra/terraform/bootstrap init
#   terraform -chdir=infra/terraform/bootstrap apply -var github_repo=<owner>/<repo>
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { "pi:layer" = "bootstrap", "pi:managed-by" = "terraform" } }
}

variable "region" {
  type    = string
  default = "ca-central-1"
}
variable "github_repo" {
  type        = string
  description = "owner/repo allowed to assume the CI roles"
}

data "aws_caller_identity" "me" {}

locals {
  account_id = data.aws_caller_identity.me.account_id
}

resource "aws_s3_bucket" "tfstate" {
  bucket = "pi-tfstate-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "gha_trust" {
  for_each = { deploy = "repo:${var.github_repo}:*", plan = "repo:${var.github_repo}:pull_request" }
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }
}

resource "aws_iam_role" "gha_deploy" {
  name               = "pi-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.gha_trust["deploy"].json
}

resource "aws_iam_role" "gha_plan" {
  name               = "pi-gha-plan"
  assume_role_policy = data.aws_iam_policy_document.gha_trust["plan"].json
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.gha_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# The deploy role creates and destroys the runtime stack (VPC, ALB, WAF, ECS, Scheduler,
# alarms, DNS records), pushes images and writes /pi/* parameters. It can pass only the
# foundation's task roles and cannot touch the audit bucket or the secret's value.
data "aws_iam_policy_document" "deploy" {
  statement {
    sid = "RuntimeStack"
    actions = [
      "ec2:*", "elasticloadbalancing:*", "wafv2:*", "ecs:*", "scheduler:*",
      "cloudwatch:*", "logs:Describe*", "logs:List*", "route53:ChangeResourceRecordSets",
      "route53:Get*", "route53:List*", "acm:Describe*", "acm:List*", "tag:GetResources",
      "servicediscovery:*", "application-autoscaling:*",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "Images"
    actions   = ["ecr:*"]
    resources = ["arn:aws:ecr:${var.region}:${local.account_id}:repository/portfolio-intel"]
  }
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "Params"
    actions   = ["ssm:GetParameter", "ssm:PutParameter", "ssm:DeleteParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/pi/*"]
  }
  statement {
    sid       = "State"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
  }
  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole", "iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/pi-*"]
  }
  statement {
    sid       = "SmokeTestToken"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:pi/*"]
  }
  statement {
    sid       = "ReadFoundation"
    actions   = ["s3:GetBucket*", "s3:ListAllMyBuckets", "firehose:Describe*", "secretsmanager:DescribeSecret", "iam:ListRoles"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  role   = aws_iam_role.gha_deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "state_bucket" { value = aws_s3_bucket.tfstate.bucket }
output "deploy_role_arn" { value = aws_iam_role.gha_deploy.arn }
output "plan_role_arn" { value = aws_iam_role.gha_plan.arn }

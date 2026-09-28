# Persistent layer (~$2-4/month): images, data, audit trail, secrets, DNS, logs, IAM.
# Survives `make down`, so `make up` never rebuilds, re-ingests or re-validates DNS.
provider "aws" {
  region = var.region
  default_tags {
    tags = { "pi:env" = var.env, "pi:layer" = "foundation", "pi:managed-by" = "terraform" }
  }
}

data "aws_caller_identity" "me" {}

locals {
  account_id = data.aws_caller_identity.me.account_id
  services   = toset(["mcp", "api", "ingest"])
}

# ---------------------------------------------------------------- images
resource "aws_ecr_repository" "app" {
  name                 = "portfolio-intel"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "keep last 20 images",
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 },
    action       = { type = "expire" }
  }] })
}

# Only the release workflow writes this; `make up` deploys whatever it names.
resource "aws_ssm_parameter" "last_green" {
  name  = "/pi/${var.env}/last_green_image_tag"
  type  = "String"
  value = "none"
  lifecycle { ignore_changes = [value] }
}

# ---------------------------------------------------------------- data
resource "aws_s3_bucket" "data" {
  bucket = "pi-data-${var.env}-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------- audit (WORM)
resource "aws_s3_bucket" "audit" {
  bucket              = "pi-audit-${var.env}-${local.account_id}"
  object_lock_enabled = true
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      mode = "GOVERNANCE" # COMPLIANCE in prod; GOVERNANCE lets you clean up a demo account
      days = var.audit_retention_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "firehose_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "pi-${var.env}-firehose"
  assume_role_policy = data.aws_iam_policy_document.firehose_trust.json
}

resource "aws_iam_role_policy" "firehose" {
  role = aws_iam_role.firehose.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow",
    Action   = ["s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket", "s3:AbortMultipartUpload"],
    Resource = [aws_s3_bucket.audit.arn, "${aws_s3_bucket.audit.arn}/*"]
  }] })
}

resource "aws_kinesis_firehose_delivery_stream" "audit" {
  name        = "pi-audit-${var.env}"
  destination = "extended_s3"
  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.audit.arn
    prefix              = "tool_calls/!{timestamp:yyyy/MM/dd}/"
    error_output_prefix = "errors/!{firehose:error-output-type}/"
    buffering_interval  = 60
    compression_format  = "GZIP"
  }
}

# ---------------------------------------------------------------- secrets
# Values are set out-of-band so they never enter Terraform state:
#   aws secretsmanager put-secret-value --secret-id pi/dev/app --secret-string file://secrets.json
resource "aws_secretsmanager_secret" "app" {
  name = "pi/${var.env}/app"
}

# ---------------------------------------------------------------- DNS (optional)
resource "aws_route53_zone" "main" {
  count = var.domain_name == "" ? 0 : 1
  name  = var.domain_name
}

resource "aws_acm_certificate" "main" {
  count             = var.domain_name == "" ? 0 : 1
  domain_name       = var.domain_name
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "cert_validation" {
  for_each = var.domain_name == "" ? {} : {
    for o in aws_acm_certificate.main[0].domain_validation_options : o.domain_name => o
  }
  zone_id = aws_route53_zone.main[0].zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "main" {
  count                   = var.domain_name == "" ? 0 : 1
  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# ---------------------------------------------------------------- logs
resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["mcp", "api", "ingest", "otel"])
  name              = "/pi/${var.env}/${each.key}"
  retention_in_days = 14
}

# ---------------------------------------------------------------- budget
resource "aws_budgets_budget" "monthly" {
  name         = "pi-${var.env}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Disposable layer (~$0.15-0.19/hour): network, ALB + WAF, ECS services, ingest schedule,
# alarms. Created by `make up`, destroyed by `make down` or auto-teardown.
provider "aws" {
  region = var.region
  default_tags {
    tags = { "pi:env" = var.env, "pi:layer" = "runtime", "pi:managed-by" = "terraform" }
  }
}

data "aws_caller_identity" "me" {}

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket = "pi-tfstate-${data.aws_caller_identity.me.account_id}"
    key    = "${var.env}/foundation.tfstate"
    region = var.region
  }
}

data "aws_ssm_parameter" "last_green" {
  name = "/pi/${var.env}/last_green_image_tag"
}

locals {
  f         = data.terraform_remote_state.foundation.outputs
  image_tag = coalesce(var.image_tag, data.aws_ssm_parameter.last_green.insecure_value)
  image     = "${local.f.ecr_repository_url}:${local.image_tag}"
  tls       = local.f.domain_name != ""
  base_url  = local.tls ? "https://${local.f.domain_name}" : "http://${aws_lb.main.dns_name}"
  otel_cfg  = file("${path.module}/../../../docker/otel-collector.aws.yaml")
  azs       = ["${var.region}a", "${var.region}b"]
  svc_common = {
    env                       = var.env
    region                    = var.region
    cluster_arn               = aws_ecs_cluster.main.arn
    image                     = local.image
    app_secret_arn            = local.f.app_secret_arn
    exec_role_arn             = local.f.exec_role_arn
    otel_log_group            = local.f.log_group_names["otel"]
    otel_config               = local.otel_cfg
    subnets                   = module.vpc.private_subnets
    service_connect_namespace = aws_service_discovery_http_namespace.main.arn
  }
}

# ---------------------------------------------------------------- network
module "vpc" {
  source             = "terraform-aws-modules/vpc/aws"
  version            = "~> 5.0"
  name               = "pi-${var.env}"
  cidr               = "10.20.0.0/16"
  azs                = local.azs
  public_subnets     = ["10.20.0.0/24", "10.20.1.0/24"]
  private_subnets    = ["10.20.10.0/24", "10.20.11.0/24"]
  enable_nat_gateway = true
  single_nat_gateway = true # dev cost saving
}

resource "aws_vpc_endpoint" "s3" { # free gateway endpoint: snapshot downloads skip the NAT
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
}

resource "aws_security_group" "alb" {
  name   = "pi-${var.env}-alb"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port   = local.tls ? 443 : 80
    to_port     = local.tls ? 443 : 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
}

# The agent needs the internet (Anthropic, Langfuse); the MCP server only HTTPS for
# ECR/Secrets Manager/Firehose/X-Ray and S3 through the gateway endpoint.
resource "aws_security_group" "api" {
  name   = "pi-${var.env}-api"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "mcp" {
  name   = "pi-${var.env}-mcp"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id, aws_security_group.api.id]
  }
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ingest" {
  name   = "pi-${var.env}-ingest"
  vpc_id = module.vpc.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------- load balancer
resource "aws_lb" "main" {
  name                       = "pi-${var.env}"
  load_balancer_type         = "application"
  subnets                    = module.vpc.public_subnets
  security_groups            = [aws_security_group.alb.id]
  idle_timeout               = 300   # streamable HTTP responses can be long-lived
  enable_deletion_protection = false # dev only, so make down can remove it
}

resource "aws_lb_target_group" "svc" {
  for_each             = { api = 8000, mcp = 8001 }
  name                 = "pi-${var.env}-${each.key}"
  port                 = each.value
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = module.vpc.vpc_id
  deregistration_delay = 15
  health_check {
    path              = "/healthz"
    interval          = 10
    healthy_threshold = 2
  }
}

resource "aws_lb_listener" "main" {
  load_balancer_arn = aws_lb.main.arn
  port              = local.tls ? 443 : 80
  protocol          = local.tls ? "HTTPS" : "HTTP"
  certificate_arn   = local.tls ? local.f.certificate_arn : null
  ssl_policy        = local.tls ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc["api"].arn
  }
}

resource "aws_lb_listener_rule" "mcp" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc["mcp"].arn
  }
  condition {
    path_pattern { values = ["/mcp", "/mcp/*"] }
  }
}

resource "aws_route53_record" "app" {
  count   = local.tls ? 1 : 0
  zone_id = local.f.zone_id
  name    = local.f.domain_name
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }
}

resource "aws_wafv2_web_acl" "main" {
  name  = "pi-${var.env}"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "pi-rate-limit"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "aws-common"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
        rule_action_override { # JSON bodies of simulations can exceed the default 8KB check
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "pi-common"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "pi-waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "main" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}

# ---------------------------------------------------------------- ECS
resource "aws_ecs_cluster" "main" {
  name = "pi-${var.env}"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_http_namespace" "main" {
  name = "pi-${var.env}"
}

module "mcp" {
  source                  = "../modules/service"
  name                    = "mcp"
  command                 = ["pi-mcp", "--transport", "http", "--port", "8001"]
  port                    = 8001
  publish_service_connect = true # the agent reaches it at http://mcp:8001/mcp
  environment = {
    PI_DATA_BUCKET  = local.f.data_bucket
    PI_AUDIT_SINK   = "firehose"
    PI_AUDIT_STREAM = local.f.audit_stream_name
  }
  secrets          = ["PI_MCP_TOKEN"]
  task_role_arn    = local.f.mcp_task_role_arn
  log_group        = local.f.log_group_names["mcp"]
  security_groups  = [aws_security_group.mcp.id]
  target_group_arn = aws_lb_target_group.svc["mcp"].arn

  env                       = local.svc_common.env
  region                    = local.svc_common.region
  cluster_arn               = local.svc_common.cluster_arn
  image                     = local.svc_common.image
  app_secret_arn            = local.svc_common.app_secret_arn
  exec_role_arn             = local.svc_common.exec_role_arn
  otel_log_group            = local.svc_common.otel_log_group
  otel_config               = local.svc_common.otel_config
  subnets                   = local.svc_common.subnets
  service_connect_namespace = local.svc_common.service_connect_namespace
  depends_on                = [aws_lb_listener_rule.mcp]
}

module "api" {
  source  = "../modules/service"
  name    = "api"
  command = ["pi-api"]
  port    = 8000
  environment = {
    PI_DATA_BUCKET = local.f.data_bucket
    PI_MCP_URL     = "http://mcp:8001/mcp"
  }
  secrets          = ["PI_MCP_TOKEN", "ANTHROPIC_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
  task_role_arn    = local.f.api_task_role_arn
  log_group        = local.f.log_group_names["api"]
  security_groups  = [aws_security_group.api.id]
  target_group_arn = aws_lb_target_group.svc["api"].arn

  env                       = local.svc_common.env
  region                    = local.svc_common.region
  cluster_arn               = local.svc_common.cluster_arn
  image                     = local.svc_common.image
  app_secret_arn            = local.svc_common.app_secret_arn
  exec_role_arn             = local.svc_common.exec_role_arn
  otel_log_group            = local.svc_common.otel_log_group
  otel_config               = local.svc_common.otel_config
  subnets                   = local.svc_common.subnets
  service_connect_namespace = local.svc_common.service_connect_namespace
  depends_on                = [aws_lb_listener.main, module.mcp]
}

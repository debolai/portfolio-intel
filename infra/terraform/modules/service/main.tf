# One Fargate service (app container + ADOT collector sidecar) behind an ALB target group.
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "name" { type = string }
variable "env" { type = string }
variable "region" { type = string }
variable "cluster_arn" { type = string }
variable "image" { type = string }
variable "command" { type = list(string) }
variable "port" { type = number }
variable "environment" { type = map(string) }
variable "secrets" {
  type        = list(string)
  description = "keys of the app secret to inject as environment variables"
}
variable "app_secret_arn" { type = string }
variable "exec_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "log_group" { type = string }
variable "otel_log_group" { type = string }
variable "otel_config" { type = string }
variable "subnets" { type = list(string) }
variable "security_groups" { type = list(string) }
variable "target_group_arn" { type = string }
variable "service_connect_namespace" { type = string }
variable "publish_service_connect" {
  type    = bool
  default = false
}
variable "desired_count" {
  type    = number
  default = 1
}
variable "cpu" {
  type    = number
  default = 512
}
variable "memory" {
  type    = number
  default = 1024
}

resource "aws_ecs_task_definition" "this" {
  family                   = "pi-${var.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.exec_role_arn
  task_role_arn            = var.task_role_arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([
    {
      name                   = var.name
      image                  = var.image
      command                = var.command
      essential              = true
      portMappings           = [{ name = var.name, containerPort = var.port, protocol = "tcp" }]
      readonlyRootFilesystem = true
      mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
      environment = [for k, v in merge(var.environment, {
        PI_ENV                      = var.env
        OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
        OTEL_SERVICE_NAME           = "pi-${var.name}"
      }) : { name = k, value = v }]
      secrets = [for k in var.secrets : { name = k, valueFrom = "${var.app_secret_arn}:${k}::" }]
      healthCheck = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:${var.port}/healthz')"]
        interval    = 10
        retries     = 3
        startPeriod = 20
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group = var.log_group, awslogs-region = var.region, awslogs-stream-prefix = var.name
        }
      }
    },
    {
      name        = "otel"
      image       = "public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1"
      essential   = false
      environment = [{ name = "AOT_CONFIG_CONTENT", value = var.otel_config }]
      secrets     = [{ name = "LANGFUSE_BASIC_AUTH", valueFrom = "${var.app_secret_arn}:LANGFUSE_BASIC_AUTH::" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group = var.otel_log_group, awslogs-region = var.region, awslogs-stream-prefix = var.name
        }
      }
    }
  ])
}

resource "aws_ecs_service" "this" {
  name                              = "pi-${var.name}"
  cluster                           = var.cluster_arn
  task_definition                   = aws_ecs_task_definition.this.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 30
  wait_for_steady_state             = false # env_up.sh waits with `aws ecs wait services-stable`

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnets
    security_groups  = var.security_groups
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = var.name
    container_port   = var.port
  }

  service_connect_configuration {
    enabled   = true
    namespace = var.service_connect_namespace
    dynamic "service" {
      for_each = var.publish_service_connect ? [1] : []
      content {
        port_name = var.name
        client_alias {
          port     = var.port
          dns_name = var.name
        }
      }
    }
  }
}

output "service_name" { value = aws_ecs_service.this.name }
output "task_definition_arn" { value = aws_ecs_task_definition.this.arn }

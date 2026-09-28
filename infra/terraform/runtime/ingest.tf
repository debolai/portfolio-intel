# Nightly ingest: the only component that writes data or touches the internet for data.
resource "aws_ecs_task_definition" "ingest" {
  family                   = "pi-ingest"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 4096
  execution_role_arn       = local.f.exec_role_arn
  task_role_arn            = local.f.ingest_task_role_arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  ephemeral_storage { size_in_gib = 30 }
  container_definitions = jsonencode([{
    name      = "ingest"
    image     = local.image
    essential = true
    # Writable root filesystem: the ingest builds data/ under /app. Services stay read-only.
    command = ["sh", "-c", "pi-ingest --sync --upload --as-of $(date -u +%F) --out data/portfolio.duckdb"]
    environment = [
      { name = "PI_ENV", value = var.env },
      { name = "PI_DATA_BUCKET", value = local.f.data_bucket },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = local.f.log_group_names["ingest"], awslogs-region = var.region,
        awslogs-stream-prefix = "ingest"
      }
    }
  }])
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "pi-${var.env}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.ingest.arn_without_revision, "${aws_ecs_task_definition.ingest.arn_without_revision}:*"] },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [local.f.exec_role_arn, local.f.ingest_task_role_arn] },
  ] })
}

resource "aws_scheduler_schedule" "ingest" {
  name                         = "pi-ingest-${var.env}"
  schedule_expression          = "cron(0 7 ? * TUE-SAT *)" # after providers publish T-1 holdings
  schedule_expression_timezone = "America/Toronto"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.ingest.arn
      launch_type         = "FARGATE"
      network_configuration {
        subnets         = module.vpc.private_subnets
        security_groups = [aws_security_group.ingest.id]
      }
    }
  }
}

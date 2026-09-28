# Least privilege per role. Only the ingest task writes data; only Firehose writes audit.
data "aws_iam_policy_document" "ecs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "exec" {
  name               = "pi-${var.env}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "exec_secret" {
  role = aws_iam_role.exec.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["secretsmanager:GetSecretValue"],
    Resource = [aws_secretsmanager_secret.app.arn]
  }] })
}

resource "aws_iam_role" "task" {
  for_each           = local.services
  name               = "pi-${var.env}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

locals {
  read_snapshots = {
    Effect   = "Allow", Action = ["s3:GetObject"],
    Resource = ["${aws_s3_bucket.data.arn}/snapshots/*"]
  }
  xray = {
    Effect   = "Allow",
    Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
    Resource = ["*"]
  }
  task_policies = {
    mcp = [local.read_snapshots, local.xray, {
      Effect   = "Allow", Action = ["firehose:PutRecord", "firehose:PutRecordBatch"],
      Resource = [aws_kinesis_firehose_delivery_stream.audit.arn]
    }]
    api = [local.read_snapshots, local.xray]
    ingest = [local.xray, {
      Effect   = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    }]
  }
}

resource "aws_iam_role_policy" "task" {
  for_each = local.services
  role     = aws_iam_role.task[each.key].id
  policy   = jsonencode({ Version = "2012-10-17", Statement = local.task_policies[each.key] })
}

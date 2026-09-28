output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
output "data_bucket" { value = aws_s3_bucket.data.bucket }
output "audit_bucket" { value = aws_s3_bucket.audit.bucket }
output "audit_stream_name" { value = aws_kinesis_firehose_delivery_stream.audit.name }
output "app_secret_arn" { value = aws_secretsmanager_secret.app.arn }
output "domain_name" { value = var.domain_name }
output "certificate_arn" { value = var.domain_name == "" ? "" : aws_acm_certificate_validation.main[0].certificate_arn }
output "zone_id" { value = var.domain_name == "" ? "" : aws_route53_zone.main[0].zone_id }
output "name_servers" { value = var.domain_name == "" ? [] : aws_route53_zone.main[0].name_servers }
output "exec_role_arn" { value = aws_iam_role.exec.arn }
output "mcp_task_role_arn" { value = aws_iam_role.task["mcp"].arn }
output "api_task_role_arn" { value = aws_iam_role.task["api"].arn }
output "ingest_task_role_arn" { value = aws_iam_role.task["ingest"].arn }
output "log_group_names" { value = { for k, g in aws_cloudwatch_log_group.svc : k => g.name } }

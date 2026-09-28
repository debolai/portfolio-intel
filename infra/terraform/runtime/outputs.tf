output "cluster_name" { value = aws_ecs_cluster.main.name }
output "base_url" { value = local.base_url }
output "image_tag" { value = local.image_tag }
output "ingest_run_task_input" {
  value = jsonencode({
    cluster        = aws_ecs_cluster.main.name
    taskDefinition = aws_ecs_task_definition.ingest.arn
    launchType     = "FARGATE"
    networkConfiguration = { awsvpcConfiguration = {
      subnets = module.vpc.private_subnets, securityGroups = [aws_security_group.ingest.id]
    } }
  })
}

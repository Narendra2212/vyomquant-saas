resource "aws_ecs_cluster" "main" {
  name = "vyomquant-cluster-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/vyomquant-${var.environment}"
  retention_in_days = 30
}

# API Task
resource "aws_ecs_task_definition" "api" {
  family                   = "vyomquant-api-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "vyomquant-api:latest" # ECR URL goes here
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "vyomquant-api-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

resource "aws_ecs_cluster" "main" {
  name = "vyomquant-cluster-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/vyomquant-${var.environment}"
  retention_in_days = 30
}

# API Task
resource "aws_ecs_task_definition" "api" {
  family                   = "vyomquant-api-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "vyomquant-api:latest" # ECR URL goes here
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "vyomquant-api-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]
}

# TEE Task
resource "aws_ecs_task_definition" "tee" {
  family                   = "vyomquant-tee-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "tee"
      image     = "vyomquant-tee:latest"
      essential = true
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "tee"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "tee" {
  name            = "vyomquant-tee-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.tee.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

# Auto Scaling for API Service
resource "aws_appautoscaling_target" "api_target" {
  max_capacity       = 10
  min_capacity       = 2
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu_policy" {
  name               = "api-cpu-target-tracking"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api_target.resource_id
  scalable_dimension = aws_appautoscaling_target.api_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api_target.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = 70.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# MDS Task
resource "aws_ecs_task_definition" "mds" {
  family                   = "vyomquant-mds-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "mds"
      image     = "vyomquant-mds:latest" # ECR URL goes here
      essential = true
      portMappings = [
        {
          containerPort = 8081
          hostPort      = 8081
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mds"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "mds" {
  name            = "vyomquant-mds-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.mds.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

# WebSocket Task
resource "aws_ecs_task_definition" "websocket" {
  family                   = "vyomquant-websocket-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "websocket"
      image     = "vyomquant-websocket:latest" # ECR URL goes here
      essential = true
      portMappings = [
        {
          containerPort = 8002
          hostPort      = 8002
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "websocket"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "websocket" {
  name            = "vyomquant-websocket-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.websocket.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.websocket.arn
    container_name   = "websocket"
    container_port   = 8002
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}

# Simulator Task (Staging/Paper Trading Only)
resource "aws_ecs_task_definition" "simulator" {
  family                   = "vyomquant-simulator-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "simulator"
      image     = "vyomquant-simulator:latest" # ECR URL goes here
      essential = true
      portMappings = [
        {
          containerPort = 8080
          hostPort      = 8080
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "simulator"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "simulator" {
  name            = "vyomquant-simulator-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.simulator.arn
  desired_count   = 0 # Should only run when VYOMQUANT_MODE=paper
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# Worker Definitions Base
locals {
  worker_definitions = {
    backtest = { cpu = 256, memory = 512, command = ["python", "-m", "backend_app.worker"] }
    dag      = { cpu = 256, memory = 512, command = ["python", "-m", "backend_app.backend.dag_worker"] }
    command  = { cpu = 256, memory = 512, command = ["python", "-m", "backend_app.workers.command_worker"] }
  }
}

resource "aws_ecs_task_definition" "workers" {
  for_each = local.worker_definitions

  family                   = "vyomquant-worker-${each.key}-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "worker-${each.key}"
      image     = "vyomquant-api:latest" # Workers use the backend image
      essential = true
      command   = each.value.command
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker-${each.key}"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "workers" {
  for_each = local.worker_definitions

  name            = "vyomquant-worker-${each.key}-service-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.workers[each.key].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

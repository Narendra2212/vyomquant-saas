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

  # CAP-01. WAS cpu = 512 / memory = 1024, AND THAT WAS SMALLER THAN WHAT IS RUNNING.
  #
  # The live api task is 1024 CPU / 4096 MB (measured against the cluster in
  # ap-southeast-1). So this file had drifted BELOW production, and applying it unchanged
  # would have halved the api's CPU and quartered its memory — converting the reported
  # memory pressure into an outage. That is recorded here because the drift is the
  # dangerous part, not the number: anyone running `terraform apply` to fix a capacity
  # complaint would have made it far worse, and nothing warned them.
  #
  # 2048 CPU because the measured 3-day maximum was 106% of 1024 — over the ceiling, which
  # is throttling. Memory stays at the 4096 production already carries: its peak was 55%,
  # and that headroom is what absorbs per-user session and connection state as the account
  # count grows. Halving CPU utilisation also gives the autoscaler room to act on a signal
  # rather than on a task that is already saturated.
  cpu                      = 2048
  memory                   = 4096
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = local.backend_image
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENV", value = var.environment },
        # BILL-02: confirmed via real AWS access (CloudFront distribution EEOXECPHQ8SR0 has
        # an ALB origin with /api/*, /ws/*, /health*, /metrics*, /openapi.json cache behaviors
        # forwarding to this service) that production sits behind CloudFront + this ALB, two
        # appending proxies -- and confirmed the real running task definition (vyomquant-api:214)
        # already carries TRUSTED_PROXY_HOPS=2, not the code's own fail-safe default of 1.
        # Declared here so a future terraform-driven redeploy of this task definition cannot
        # silently drop back to the default and start misattributing/rejecting every genuine
        # Razorpay webhook. See backend_app/routers/billing.py's own
        # _TRUSTED_PROXY_HOPS_DEFAULT comment and this finding's ledger entry for the evidence.
        { name = "TRUSTED_PROXY_HOPS", value = "2" }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        },
        {
          name      = "SUPABASE_SERVICE_ROLE_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_service_role_key"
        },
        {
          name      = "SUPABASE_ANON_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_anon_key"
        },
        {
          name      = "SUPABASE_JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_jwt_secret"
        },
        {
          name      = "MASTER_ENCRYPTION_KEYS"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/master_encryption_keys"
        },
        {
          name      = "CREDENTIAL_VAULT_SALT"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/credential_vault_salt"
        },
        {
          name      = "JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/jwt_secret"
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

  # CAP-01. THE MEASURED KILL MECHANISM, AND WHY THIS IS THE FIX RATHER THAN MORE MEMORY.
  #
  # The only stopped tasks in the whole cluster are two api tasks, and neither died of
  # memory. Both read:
  #
  #     Task failed ELB health checks in (target-group .../vyomquant-api-tg/...)
  #
  # There is no OutOfMemoryError anywhere, no service event mentioning memory, and Redis
  # sits at 2-4% of capacity with zero evictions. What there IS: the api's CPU maximum at
  # 106% of a single vCPU.
  #
  # That composes into a loop. One saturated task stops answering `/health/ready` inside
  # its 5s timeout -- that endpoint probes dependencies at 3s each, so a CPU-starved task
  # misses it -- the ALB marks it unhealthy after 3 intervals, ECS kills and replaces it,
  # and every request in the gap fails. With production running ONE task, that gap is a
  # total outage. To a user it reads exactly like memory starvation: hangs, then errors.
  #
  # `health_check_grace_period_seconds` is the missing piece. Without it a REPLACEMENT task
  # is health-checked while it is still importing the application and opening pools, so a
  # cold start can be killed before it ever serves a request -- the loop sustains itself
  # once it begins. 120s is chosen against the probe's own cost, not guessed: 3 intervals
  # of 30s is 90s of grace before the ALB may act, plus margin for image pull.
  health_check_grace_period_seconds = 120

  # A deploy must never drop below the capacity that is serving traffic. At `desired_count`
  # 2 this keeps one task in rotation while the other is replaced, which is also what makes
  # a rollout survivable under load rather than a self-inflicted version of the loop above.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

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

# RT-23: the TEE task definition and service that used to live here are deleted, not
# just left undeployed. backend_app/tee/main.py crashed on import
# (`from backend_app.core.fleet_manager import get_fleet_manager` -- that module and
# symbol never existed) and real AWS evidence (gathered the same session as this
# deletion) confirmed zero ECS services for it had ever actually been created --
# nothing in production referenced these two resources. command_worker.py already
# consumes the same COMMAND_STREAM='command_queue' start_bot/stop_bot surface this was
# meant to cover, and is itself deployed and tested; repairing tee/main.py's import
# without also giving it a genuinely separate queue would have made both processes
# consume the same command, a live double-execution hazard against real exchange
# accounts (documented, not hypothetical: see tests/test_ml_node_detection_is_one_
# vocabulary.py::TestTheTeeGateIsUnreachable and this finding's own ledger entry,
# RT-23, for the full investigation). If a genuinely separate execution lane is wanted
# later, it needs its own queue/consumer-group and the same safety wiring RT-15/16/21
# already gave the rest of live execution -- a deliberate design decision, not a
# one-line repair, so it is not reconstructed here.

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
  # CAP-01. WAS cpu = 256 / memory = 512, ALSO SMALLER THAN WHAT IS RUNNING.
  #
  # The live mds task is 1024 CPU / 3072 MB. Same drift as the api above and the same
  # hazard: applying this file unchanged would have cut mds to a quarter of its CPU and a
  # sixth of its memory.
  #
  # Matched to production rather than trimmed to measured use. Its memory sat at 4% of
  # 3072 — but mds holds candle state for EVERY subscribed symbol, so memory is the axis
  # that grows with the account count, and 4% today says nothing about 1000 users each
  # watching their own symbols. CPU peaked at 65% of 1024, which is real headroom, so CPU
  # is left where it is. If anything here needs raising later it is memory, and the
  # autoscaler cannot help: this service is a singleton by design.
  cpu                      = 1024
  memory                   = 3072
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name = "mds"
      # NOT a `vyomquant-mds` image, despite that repository existing. The live
      # vyomquant-mds:5 task definition references .../vyomquant-api, so the MDS runs the
      # backend image with a different command -- measured, not assumed.
      image     = local.backend_image
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
        },
        {
          name      = "SUPABASE_SERVICE_ROLE_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_service_role_key"
        },
        {
          name      = "SUPABASE_ANON_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_anon_key"
        },
        {
          name      = "SUPABASE_JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_jwt_secret"
        },
        {
          name      = "MASTER_ENCRYPTION_KEYS"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/master_encryption_keys"
        },
        {
          name      = "CREDENTIAL_VAULT_SALT"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/credential_vault_salt"
        },
        {
          name      = "JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/jwt_secret"
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

# WS-01: the dedicated websocket task definition/service are removed along with ws_server.py
# itself (deleted this remediation effort) and alb.tf's matching target group/listener rule --
# see the comment there for the real-AWS evidence that none of this ever had live traffic.

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
      name = "simulator"
      # Well-formed but currently unresolvable: no vyomquant-simulator ECR repository
      # exists and no simulator task definition is ACTIVE. See
      # var.simulator_image_repository.
      image     = local.simulator_image
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
        },
        {
          name      = "SUPABASE_SERVICE_ROLE_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_service_role_key"
        },
        {
          name      = "SUPABASE_ANON_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_anon_key"
        },
        {
          name      = "SUPABASE_JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_jwt_secret"
        },
        {
          name      = "MASTER_ENCRYPTION_KEYS"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/master_encryption_keys"
        },
        {
          name      = "CREDENTIAL_VAULT_SALT"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/credential_vault_salt"
        },
        {
          name      = "JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/jwt_secret"
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
    # CAP-01 SIZING, FROM MEASURED USE RATHER THAN GUESSWORK.
    #
    # A 3-day CloudWatch window over the live cluster (ap-southeast-1) said the constraint
    # is CPU, not memory, and it is not close:
    #
    #   service              mem% avg/max   cpu% avg/max   allotted
    #   api                      41 / 55        8 / 106     4096MB / 1024
    #   dag                      30 / 31        0 / 100      512MB /  256
    #   command                  17 / 18        0 / 100      512MB /  256
    #   marketplace_expiry       13 / 14        0 /  99      512MB /  256
    #   exchange_fills           16 / 21        0 /  94     1024MB /  512
    #   backtest                  8 /  8        0 /  62      512MB /  256
    #   mds                       4 /  4        0 /  65     3072MB / 1024
    #
    # Every memory maximum is under 56%. Every CPU maximum on a 256-unit (0.25 vCPU)
    # worker pegs at 94-100%, and the api exceeded 100% — which is throttling, not load.
    # Average CPU near 0% with maxima at the ceiling is the signature of bursty work on a
    # task too small to absorb a burst: the queue drains in steps, which presents as the
    # app hanging rather than as an out-of-memory error.
    #
    # So CPU doubles on the three workers that peg, and memory is LEFT ALONE. Cutting
    # memory to match current use would remove the headroom that is keeping these from
    # actually starving once 1000 users put real queue depth behind them.
    # FARGATE CONSTRAINT, learned by having it refuse: 512 CPU does not pair with 512 MB.
    # `RegisterTaskDefinition` answers "No Fargate configuration exists for given values:
    # 512 CPU, 512 memory". 512 CPU's smallest legal memory is 1024, so every worker raised
    # to 512 CPU goes to 1024 MB whether or not it needs the memory. Had this file kept
    # 512/512, `terraform apply` would have failed outright.
    backtest = { cpu = 512, memory = 1024, command = ["python", "-m", "backend_app.worker"] }
    dag      = { cpu = 512, memory = 1024, command = ["python", "-m", "backend_app.backend.dag_worker"] }
    command  = { cpu = 512, memory = 1024, command = ["python", "-m", "backend_app.workers.command_worker"] }
    # INF-03 / marketplace Requirement 11.8: the subscription expiry sweep (30s). It was built
    # and tested but had no deployment entry, so paid marketplace access never ended.
    # Raised with the others: its CPU maximum was 99% of 256 units. It cannot be scaled
    # OUT -- a second task runs the same 30s sweep and expires the same rows twice -- so a
    # bigger task is the only axis available. 1024 MB because of the Fargate pairing rule
    # noted above, not because it needs the memory (it peaked at 14% of 512).
    marketplace_expiry = { cpu = 512, memory = 1024, command = ["python", "-m", "backend_app.workers.marketplace_expiry_worker"] }
    # OPS-TRAIN-01: backend_app.backend.training_worker.TrainingWorker claims QUEUED rows
    # via the atomic `SET status='RUNNING' ... WHERE status='QUEUED'` transition described
    # in that module's own docstring and runs them -- but nothing has ever run this process
    # in production, so every training job a user submits stays QUEUED forever. The
    # module's own run_worker() docstring says it is "meant to be deployed as a separate
    # process", never wired into main.py's startup, specifically so a fitting job can never
    # share the API process's interpreter, saturate its Redis connection, or block
    # order/execution request handling (Requirement 16.6, TrainingIsolator's own docstring).
    # Sized well above the other workers: core/ml_safety.py's MemoryMonitor and
    # backend/ml_training_policy.py's own resource tiers cap an individual training run at
    # up to 4096 MB (CAP_MAX_MEMORY / the `max_memory_mb=4_096` tier) -- 512 MB (every other
    # worker here) would not even fit one job at its own documented ceiling, let alone the
    # interpreter plus torch/tensorflow/catboost/scikit-learn already loaded. 2 vCPU / 8192
    # MB leaves roughly 2x headroom over that 4 GB per-job cap for everything else resident
    # in the process.
    training = { cpu = 2048, memory = 8192, command = ["python", "-m", "backend_app.backend.training_worker"] }
    # RT-16: the host for the private venue fill stream. The three links it starts
    # (ExchangeWebSocketListener -> EventListener -> EventRouter) each existed and none had
    # a caller, so a fill was only ever discovered by the watchdog's poll.
    #
    # IT MUST NOT LIVE IN THE WEB PROCESS, which is why it is a worker at all: gunicorn
    # runs --workers ${WORKERS:-2} and every worker runs the lifespan hook, so a stream
    # started there opens two authenticated private sockets per (tenant, venue) and gives
    # every fill two writers. The worker takes a Redis leader lease
    # (core/leader_lease.try_acquire_or_renew) so even multiple TASKS of this service
    # elect one streamer, and it closes its sockets the moment it loses the lease.
    #
    # Sized above the 256/512 light workers and below mds's 1024/3072. It holds one
    # authenticated websocket per live (tenant, venue) pair plus an unbounded
    # asyncio.PriorityQueue of in-flight events -- more resident state than a queue-polling
    # worker, far less than mds, which holds candle state for every subscribed symbol.
    exchange_fills = { cpu = 512, memory = 1024, command = ["python", "-m", "backend_app.workers.exchange_fill_worker"] }
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
      name = "worker-${each.key}"
      # Workers use the backend image, differing only by command. The live worker task
      # definitions all reference .../vyomquant-api at the same commit SHA as the API.
      image     = local.backend_image
      essential = true
      command   = each.value.command
      environment = [
        { name = "ENV", value = var.environment }
      ]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/database_url"
        },
        {
          name      = "SUPABASE_SERVICE_ROLE_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_service_role_key"
        },
        {
          name      = "SUPABASE_ANON_KEY"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_anon_key"
        },
        {
          name      = "SUPABASE_JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/supabase_jwt_secret"
        },
        {
          name      = "MASTER_ENCRYPTION_KEYS"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/master_encryption_keys"
        },
        {
          name      = "CREDENTIAL_VAULT_SALT"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/credential_vault_salt"
        },
        {
          name      = "JWT_SECRET"
          valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/${var.environment}/jwt_secret"
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

# ─────────────────────────────────────────────────────────────────────────────────
# INF-02: the registry is DERIVED, not written down.
#
# Hardcoding "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com" would pin the account id
# into the configuration, which is both a disclosure and a thing that silently breaks in
# any other account. `data.aws_caller_identity.current` is already declared in iam.tf and
# var.aws_region already exists, so the registry host composes from facts the provider
# supplies.
# ─────────────────────────────────────────────────────────────────────────────────
locals {
  ecr_registry = join("", [
    data.aws_caller_identity.current.account_id,
    ".dkr.ecr.", var.aws_region, ".amazonaws.com",
  ])

  #: The one image that serves the API, the MDS and every worker. See the note on
  #: var.backend_image_repository for the measurement behind "one image".
  backend_image = join("", [
    local.ecr_registry, "/", var.backend_image_repository, ":", var.image_tag,
  ])

  #: Well-formed, and currently unresolvable -- no such ECR repository exists. See the
  #: note on var.simulator_image_repository.
  simulator_image = join("", [
    local.ecr_registry, "/", var.simulator_image_repository, ":", var.image_tag,
  ])
}

# ══════════════════════════════════════════════════════════════════════════════
#  CAP-01 — WORKER AUTOSCALING, AND THE THREE WORKERS THAT MUST NOT HAVE IT
#
#  The brief was "scale up and down with load". Three of the six workers CANNOT be
#  horizontally scaled safely, and scaling them would cause incidents rather than
#  relieve load. The dangerous one is named first because it moves real money.
#
#  command           NEVER. `command_worker.py` consumes COMMAND_STREAM='command_queue',
#                    which carries start_bot/stop_bot. This file's own note at the
#                    `worker_definitions` map records why a second consumer of that
#                    stream is "a live double-execution hazard against real exchange" —
#                    two tasks can take the same start command and open a position twice.
#                    Its CPU does peg at 100%, so it gets a BIGGER task (above), not more
#                    tasks. That is the only safe axis for it.
#
#  marketplace_expiry NEVER. A 30-second sweep over due subscriptions (Requirement 11.8).
#                    A second task runs the same sweep concurrently and expires the same
#                    rows twice.
#
#  exchange_fills    POINTLESS, so no. It takes a Redis leader lease
#                    (core/leader_lease.try_acquire_or_renew) specifically so that extra
#                    tasks elect one streamer; the others would idle at full cost while
#                    holding authenticated venue sockets open for nothing.
#
#  The three below are safe because each one CLAIMS its work exclusively:
#
#  backtest          Redis Streams consumer group 'backtest_workers' (worker.py). XREADGROUP
#                    hands a message to exactly one consumer in the group.
#  dag               the same consumer-group shape.
#  training          claims rows with an atomic
#                    `SET status='RUNNING' ... WHERE status='QUEUED'` transition
#                    (training_worker's own docstring), so two tasks cannot take one job.
#
#  Scaled on MEMORY as well as CPU, because the reported symptom was memory pressure. The
#  CPU signal is what the measurements actually implicate, but a queue deep enough to
#  change that is exactly when the memory signal earns its place — and target tracking
#  takes whichever policy demands more capacity.
# ══════════════════════════════════════════════════════════════════════════════

locals {
  #: Workers whose work is exclusively claimed, so a second task cannot duplicate it.
  #: Adding a key here is a statement that concurrent execution of that worker is SAFE.
  scalable_workers = {
    backtest = { min = 1, max = 6 }
    dag      = { min = 1, max = 6 }
    training = { min = 1, max = 4 }
  }
}

resource "aws_appautoscaling_target" "worker_target" {
  for_each = local.scalable_workers

  max_capacity       = each.value.max
  min_capacity       = each.value.min
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.workers[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "worker_cpu" {
  for_each = local.scalable_workers

  name               = "vyomquant-worker-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.worker_target[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.worker_target[each.key].scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker_target[each.key].service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 60.0

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    # Out fast, in slow. A queue that just spiked will spike again, and a worker removed
    # 60 seconds after a burst is a worker that has to be added again immediately —
    # flapping costs more than the task did.
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}

resource "aws_appautoscaling_policy" "worker_memory" {
  for_each = local.scalable_workers

  name               = "vyomquant-worker-${each.key}-memory"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.worker_target[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.worker_target[each.key].scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker_target[each.key].service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 70.0

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }

    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}

# ── The API's second signal ───────────────────────────────────────────────────
#
# `api_cpu_policy` above already tracks CPU at 70%. Memory is added as a second policy
# rather than replacing it: the api measured 55% memory at its peak on one task, and the
# headroom that leaves disappears as connection and session state multiplies across 1000
# users. Target tracking honours whichever policy asks for more tasks, so this can only
# add capacity, never withhold it.
resource "aws_appautoscaling_policy" "api_memory_policy" {
  name               = "vyomquant-api-memory"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api_target.resource_id
  scalable_dimension = aws_appautoscaling_target.api_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api_target.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 70.0

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }

    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}

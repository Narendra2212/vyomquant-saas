# SEC-17: the real ALB's security group opened both 80 and 443 to the entire internet,
# confirmed directly against the real account (sg-067b73e186fc1a635) -- not just this
# source. With the real ALB having only an HTTP:80 listener (no HTTPS), every request,
# including Authorization headers and broker credentials, was served in clear text
# directly to anyone who connected to the ALB's own public DNS name, bypassing
# CloudFront's TLS termination entirely -- a live cleartext-credentials exposure, not a
# theoretical one. Applied directly via ec2:AuthorizeSecurityGroupIngress/
# RevokeSecurityGroupIngress (not terraform apply -- no resource in this account has ever
# been imported into terraform state, see INF-02): port 80 now restricted to CloudFront's
# own managed prefix list (com.amazonaws.global.cloudfront.origin-facing = pl-31a34658 in
# this region), so only CloudFront's edge network can reach the ALB at all. Verified end to
# end: the real app domain through CloudFront still returns 200; the ALB's own raw hostname
# now times out; the target stayed healthy throughout.
#
# Port 443 was NOT given an equivalent prefix-list rule: AuthorizeSecurityGroupIngress for
# it failed with RulesPerSecurityGroupLimitExceeded -- the CloudFront prefix list has 49
# entries, and each one counts toward this account's default 60-rules-per-security-group
# quota, which port 80's own prefix-list rule (plus the two pre-existing plain-CIDR rules)
# had nearly exhausted. Since the real ALB has no HTTPS listener at all today (confirmed via
# DescribeListeners), port 443 having NO ingress rule is strictly safer than leaving it open
# to 0.0.0.0/0 and breaks nothing live -- so the old 0.0.0.0/0:443 rule was revoked without
# a replacement. Re-adding a CloudFront-scoped :443 rule once a real HTTPS listener exists
# will need either an EC2 security-group-rules-per-group quota increase or consolidating
# this group's other rule usage first.
resource "aws_security_group" "alb" {
  name        = "vyomquant-alb-sg-${var.environment}"
  description = "Security group for Application Load Balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTP from CloudFront only"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "ecs_tasks" {
  name        = "vyomquant-ecs-tasks-sg-${var.environment}"
  description = "Allow inbound access from the ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Allow HTTP from ALB"
    protocol        = "tcp"
    from_port       = 8000
    to_port         = 8080
    security_groups = [aws_security_group.alb.id]
  }

  # Allow TEE, MDS, Workers to communicate internally
  ingress {
    description = "Allow internal ECS communication"
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    self        = true
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "redis" {
  name        = "vyomquant-redis-sg-${var.environment}"
  description = "Security group for Redis"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Allow from ECS tasks"
    protocol        = "tcp"
    from_port       = 6379
    to_port         = 6379
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

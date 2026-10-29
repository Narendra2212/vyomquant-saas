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

  # CI-05: was open to 0.0.0.0/0. An ALB's only legitimate egress target is the backend it
  # forwards requests to and returns responses from, which lives inside this VPC -- it has
  # no reason to originate a connection anywhere else. Restricted to the VPC's own CIDR
  # rather than ecs_tasks' security group specifically: that group's own ingress already
  # references this ALB's security group (line below), so referencing it back here would
  # create a circular resource dependency terraform plan refuses outright.
  egress {
    description = "To backends inside this VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
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

  # CI-05: was open to 0.0.0.0/0. ECS tasks genuinely need broad internet egress --
  # Supabase, the payment provider (Razorpay) and every exchange ccxt talks to are
  # all at IP ranges this account does not control and cannot practically enumerate -- so
  # this one is not narrowed here. See terraform/.trivyignore for the explicit, reviewed
  # exception this is accepted under, rather than leaving an undocumented CRITICAL finding.
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# INF-02: this group is REAL and is now tracked in terraform state.
#
# It was previously described in this entry's own ledger text as "orphaned" -- meaning only
# that nothing else in this root module referenced it after redis.tf's managed replication
# group became a data source. It is not orphaned in the sense that matters: the live group
# sg-00c4bac4f05c9922b carries all six running ElastiCache nodes
# (vyomquant-redis-production-0001-00{1,2,3} and vyomquant-redis-production-cmd-00{1,2,3},
# every one `available`), so deleting the configuration would have dropped the one accurate
# description of a load-bearing production resource. It was imported instead.
#
# Three fields below are written to match the LIVE group exactly, because `name` and
# `description` are both ForceNew on aws_security_group -- a mismatch does not produce a
# harmless diff, it produces "must be replaced", and replacing this group would detach six
# live Redis nodes:
#
#   name        already matched ("vyomquant-redis-sg-production" via var.environment)
#   description was "Security group for Redis"; the live value is the longer string below
#   ingress     referenced the MANAGED aws_security_group.ecs_tasks, which does not exist
#               in this account at all. Production runs every ECS task on the VPC's DEFAULT
#               security group (sg-0262aed02d8df0356), which is what the live rule actually
#               admits. existing_vpc.tf already declares a data source for precisely that
#               group, under confusingly the same name, so the reference is repointed at
#               the data source -- the real thing -- rather than the fictional resource.
#
# That ECS tasks run on the VPC default security group is a posture problem in its own
# right (a shared, implicitly-permissive group), but it is a live-infrastructure decision
# with real blast radius, not a documentation fix, so it is recorded rather than changed
# here. terraform's job in this finding is to describe production accurately first.
resource "aws_security_group" "redis" {
  name        = "vyomquant-redis-sg-${var.environment}"
  description = "Security group for Redis - allows access only from existing ECS tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Allow Redis from existing ECS tasks"
    protocol        = "tcp"
    from_port       = 6379
    to_port         = 6379
    security_groups = [data.aws_security_group.ecs_tasks.id]
  }

  # CI-05: was open to 0.0.0.0/0 BOTH here and live. A managed ElastiCache node has no
  # legitimate reason to originate a connection to the public internet; restricted to this
  # VPC's own CIDR (intra-VPC only), which still covers the intra-cluster replication AWS's
  # managed machinery needs -- all six node addresses (10.0.131.77, .134.167, .137.54,
  # .145.139, .145.189, .156.27) are inside 10.0.0.0/16.
  #
  # Unlike the ALB's egress, this one was NOT in force when it was written: the committed
  # configuration said var.vpc_cidr while the live group still egressed to 0.0.0.0/0 -- a
  # second instance of CI-05's "control committed, never applied" gap, found by comparing
  # this file against the live group rather than by re-reading the file. Applied directly
  # (authorize the VPC-CIDR rule first, then revoke 0.0.0.0/0, so the nodes were never
  # without egress) and verified by the application's own /health continuing to report
  # redis connected afterwards.
  egress {
    description = "CI-05/INF-02: intra-VPC only"
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = [var.vpc_cidr]
  }
}

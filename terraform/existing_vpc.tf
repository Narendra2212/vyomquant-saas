# Data sources for existing production VPC infrastructure
# This allows Redis to be deployed into the existing VPC without recreating networking

data "aws_vpc" "existing" {
  id = "vpc-0f3e7b3f243fbc133"
}

data "aws_subnet" "private_1" {
  vpc_id     = data.aws_vpc.existing.id
  cidr_block = "10.0.128.0/20"
}

data "aws_subnet" "private_2" {
  vpc_id     = data.aws_vpc.existing.id
  cidr_block = "10.0.144.0/20"
}

data "aws_security_group" "ecs_tasks" {
  id = "sg-0262aed02d8df0356"
}

# Comment out availability zones since we're not creating new subnets
# data "aws_availability_zones" "available" {
#   state = "available"
# }

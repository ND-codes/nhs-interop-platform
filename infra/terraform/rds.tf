resource "random_password" "db" {
  length  = 24
  special = true
}

resource "aws_db_subnet_group" "hapi" {
  name       = "${local.name}-hapi"
  subnet_ids = module.vpc.database_subnets
  tags       = local.common_tags
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds-sg"
  description = "HAPI FHIR Postgres — ingress from EKS nodes only"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Postgres from EKS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.common_tags
}

resource "aws_db_instance" "hapi" {
  identifier     = "${local.name}-hapi"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = 200
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.rds.arn

  db_name  = "hapi"
  username = "hapi"
  password = random_password.db.result

  multi_az               = var.environment == "prod" ? true : false
  db_subnet_group_name   = aws_db_subnet_group.hapi.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  backup_retention_period = var.environment == "prod" ? 35 : 7
  backup_window           = "02:00-03:00"
  maintenance_window      = "Sun:03:30-Sun:04:30"
  deletion_protection     = var.environment == "prod"
  skip_final_snapshot     = var.environment != "prod"

  performance_insights_enabled = true
  monitoring_interval          = 60
  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = local.common_tags
}

# Store the generated password in Secrets Manager — never a plain output.
resource "aws_secretsmanager_secret" "db" {
  name       = "${local.name}/hapi-db"
  kms_key_id = aws_kms_key.rds.arn
  tags       = local.common_tags
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = aws_db_instance.hapi.username
    password = random_password.db.result
    host     = aws_db_instance.hapi.address
    port     = aws_db_instance.hapi.port
    dbname   = aws_db_instance.hapi.db_name
  })
}

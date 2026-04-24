# Customer-managed KMS keys for envelope encryption everywhere that touches
# patient data. Keys are rotated automatically and scoped by service.

resource "aws_kms_key" "rds" {
  description             = "${local.name} RDS encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.common_tags
}
resource "aws_kms_alias" "rds" {
  name          = "alias/${local.name}-rds"
  target_key_id = aws_kms_key.rds.key_id
}

resource "aws_kms_key" "s3" {
  description             = "${local.name} S3 encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.common_tags
}
resource "aws_kms_alias" "s3" {
  name          = "alias/${local.name}-s3"
  target_key_id = aws_kms_key.s3.key_id
}

resource "aws_kms_key" "eks" {
  description             = "${local.name} EKS secrets encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.common_tags
}
resource "aws_kms_alias" "eks" {
  name          = "alias/${local.name}-eks"
  target_key_id = aws_kms_key.eks.key_id
}

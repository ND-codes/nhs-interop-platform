resource "aws_s3_bucket" "dlq" {
  bucket        = "${local.name}-dlq-${random_id.suffix.hex}"
  force_destroy = var.environment != "prod"
  tags          = local.common_tags
}

resource "aws_s3_bucket_versioning" "dlq" {
  bucket = aws_s3_bucket.dlq.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dlq" {
  bucket = aws_s3_bucket.dlq.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "dlq" {
  bucket                  = aws_s3_bucket.dlq.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "dlq" {
  bucket = aws_s3_bucket.dlq.id

  rule {
    id     = "transition-and-expire"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    expiration {
      days = 2555 # ~7 years, matches NHS records retention
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# Object Lock is only enabled on the audit bucket in prod to avoid
# accidental cost in dev.
resource "aws_s3_bucket_object_lock_configuration" "dlq" {
  count  = var.environment == "prod" ? 1 : 0
  bucket = aws_s3_bucket.dlq.id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 2555
    }
  }
}

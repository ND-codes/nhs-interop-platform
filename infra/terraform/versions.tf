terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  # Remote state — uncomment and point at a real S3/DynamoDB pair for CI.
  # backend "s3" {
  #   bucket         = "nhs-interop-tfstate"
  #   key            = "interop/terraform.tfstate"
  #   region         = "eu-west-2"
  #   dynamodb_table = "nhs-interop-tflock"
  #   encrypt        = true
  # }
}

# The credentials are deliberately fake and validation is skipped, because CI
# runs `terraform init -backend=false`, `validate` and `fmt -check` only. It
# never plans or applies, so it needs no AWS account and holds no secret.
#
# default_tags was previously a commented-out second provider block, while
# docs/security.md cited it as the evidence that every resource carries
# DataClass=patient-identifiable. It did not, and DataClass appeared exactly
# once in this directory, inside that comment. Tagging matters here beyond
# cost allocation: the data-classification tag is what an access review and a
# DSPT assessment use to find the resources holding patient data.
#
# default_tags covers every taggable resource without each one repeating the
# map. Some resources in this configuration take no tags argument at all
# (aws_kms_alias, aws_s3_bucket_versioning, aws_s3_bucket_public_access_block
# and the other bucket sub-resources), so the honest claim is every taggable
# resource, not every resource.
provider "aws" {
  region = var.aws_region

  access_key = "mock"
  secret_key = "mock"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      Owner       = "platform-engineering"
      DataClass   = "patient-identifiable"
      ManagedBy   = "terraform"
    }
  }
}

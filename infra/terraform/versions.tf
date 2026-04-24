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

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project    = var.project
      Environment = var.environment
      Owner       = "platform-engineering"
      DataClass   = "patient-identifiable"
      ManagedBy   = "terraform"
    }
  }
}

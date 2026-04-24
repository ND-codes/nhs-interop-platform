variable "project" {
  type        = string
  default     = "nhs-interop"
  description = "Short project slug used for tagging and naming."
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Environment (dev | staging | prod)."
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev, staging, prod."
  }
}

variable "aws_region" {
  type        = string
  default     = "eu-west-2"
  description = "AWS region — eu-west-2 (London) keeps patient data in-UK."
}

variable "vpc_cidr" {
  type        = string
  default     = "10.40.0.0/16"
  description = "Parent CIDR for the VPC."
}

variable "eks_version" {
  type        = string
  default     = "1.29"
  description = "EKS control plane version."
}

variable "eks_node_instance_types" {
  type        = list(string)
  default     = ["t3.large"]
  description = "EC2 instance types for the managed node group."
}

variable "eks_node_min_size" {
  type    = number
  default = 2
}

variable "eks_node_max_size" {
  type    = number
  default = 6
}

variable "eks_node_desired_size" {
  type    = number
  default = 3
}

variable "db_instance_class" {
  type        = string
  default     = "db.t4g.medium"
  description = "RDS instance class for HAPI FHIR backing store."
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

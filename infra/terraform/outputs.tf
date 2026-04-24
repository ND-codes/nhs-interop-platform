output "vpc_id" {
  value       = module.vpc.vpc_id
  description = "VPC ID."
}

output "private_subnet_ids" {
  value       = module.vpc.private_subnets
  description = "Private subnet IDs — workloads live here."
}

output "eks_cluster_name" {
  value       = module.eks.cluster_name
  description = "EKS cluster name."
}

output "eks_oidc_provider_arn" {
  value       = module.eks.oidc_provider_arn
  description = "OIDC provider ARN for IRSA."
}

output "interop_irsa_role_arn" {
  value       = module.interop_irsa.iam_role_arn
  description = "IAM role ARN the interop service accounts assume."
}

output "rds_endpoint" {
  value       = aws_db_instance.hapi.address
  description = "HAPI FHIR RDS endpoint (Postgres)."
  sensitive   = true
}

output "dlq_bucket" {
  value       = aws_s3_bucket.dlq.id
  description = "S3 bucket where failed HL7 messages land."
}

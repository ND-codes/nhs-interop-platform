module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = "${local.name}-eks"
  cluster_version = var.eks_version

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  # API endpoint is private by default; grant access to the CI runner via
  # the public endpoint + CIDR allow-list when needed.
  cluster_endpoint_public_access       = true
  cluster_endpoint_public_access_cidrs = ["0.0.0.0/0"] # tighten in prod

  # Secrets encrypted at rest with our own CMK.
  cluster_encryption_config = {
    resources        = ["secrets"]
    provider_key_arn = aws_kms_key.eks.arn
  }

  # Enable the control plane logs DSPT wants (audit + authenticator).
  cluster_enabled_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  eks_managed_node_groups = {
    default = {
      min_size       = var.eks_node_min_size
      max_size       = var.eks_node_max_size
      desired_size   = var.eks_node_desired_size
      instance_types = var.eks_node_instance_types
      capacity_type  = "ON_DEMAND"
      labels = {
        "workload" = "interop"
      }
    }
  }

  # Add-ons — explicitly versioned so upgrades are controlled.
  cluster_addons = {
    coredns    = { most_recent = true }
    kube-proxy = { most_recent = true }
    vpc-cni    = { most_recent = true }
    aws-ebs-csi-driver = {
      most_recent              = true
      service_account_role_arn = module.ebs_csi_irsa.iam_role_arn
    }
  }

  tags = local.common_tags
}

# IRSA role for the EBS CSI driver (needed by the HAPI FHIR PV).
module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name             = "${local.name}-ebs-csi"
  attach_ebs_csi_policy = true
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
  tags = local.common_tags
}

# IRSA role the interop workloads assume to read Secrets Manager and write
# to the DLQ S3 bucket.
module "interop_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name = "${local.name}-interop-sa"
  role_policy_arns = {
    secrets = aws_iam_policy.interop_secrets.arn
    dlq     = aws_iam_policy.interop_dlq.arn
  }
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["interop:ingest", "interop:transform", "interop:pds-client"]
    }
  }
  tags = local.common_tags
}

resource "aws_iam_policy" "interop_secrets" {
  name = "${local.name}-interop-secrets"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:${local.name}/*"
      }
    ]
  })
}

resource "aws_iam_policy" "interop_dlq" {
  name = "${local.name}-interop-dlq"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:PutObjectAcl"]
        Resource = "${aws_s3_bucket.dlq.arn}/*"
      }
    ]
  })
}

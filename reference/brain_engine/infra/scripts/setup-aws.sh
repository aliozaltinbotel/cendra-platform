#!/bin/bash
# ═══════════════════════════════════════════════════
# Brain Engine — AWS Initial Setup Script
# Run this ONCE to bootstrap AWS infrastructure
# ═══════════════════════════════════════════════════

set -euo pipefail

PROJECT="brain-engine"
REGION="${AWS_REGION:-us-east-1}"
STATE_BUCKET="${PROJECT}-terraform-state"
LOCK_TABLE="${PROJECT}-tf-lock"

echo "══════════════════════════════════════════"
echo "  Brain Engine — AWS Setup"
echo "  Region: $REGION"
echo "══════════════════════════════════════════"

# ── Step 1: Check AWS CLI ──
echo ""
echo "▶ Step 1: Checking AWS CLI..."
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

aws sts get-caller-identity > /dev/null 2>&1 || {
    echo "❌ AWS credentials not configured. Run: aws configure"
    exit 1
}

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "✅ AWS Account: $ACCOUNT_ID | Region: $REGION"

# ── Step 2: Create S3 bucket for Terraform state ──
echo ""
echo "▶ Step 2: Creating Terraform state bucket..."
if aws s3api head-bucket --bucket "$STATE_BUCKET" 2>/dev/null; then
    echo "✅ Bucket $STATE_BUCKET already exists"
else
    aws s3api create-bucket \
        --bucket "$STATE_BUCKET" \
        --region "$REGION" \
        $([ "$REGION" != "us-east-1" ] && echo "--create-bucket-configuration LocationConstraint=$REGION")

    aws s3api put-bucket-versioning \
        --bucket "$STATE_BUCKET" \
        --versioning-configuration Status=Enabled

    aws s3api put-bucket-encryption \
        --bucket "$STATE_BUCKET" \
        --server-side-encryption-configuration '{
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        }'

    aws s3api put-public-access-block \
        --bucket "$STATE_BUCKET" \
        --public-access-block-configuration '{
            "BlockPublicAcls": true,
            "IgnorePublicAcls": true,
            "BlockPublicPolicy": true,
            "RestrictPublicBuckets": true
        }'

    echo "✅ Created bucket: $STATE_BUCKET"
fi

# ── Step 3: Create DynamoDB table for Terraform locking ──
echo ""
echo "▶ Step 3: Creating Terraform lock table..."
if aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$REGION" > /dev/null 2>&1; then
    echo "✅ Table $LOCK_TABLE already exists"
else
    aws dynamodb create-table \
        --table-name "$LOCK_TABLE" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION"

    echo "✅ Created table: $LOCK_TABLE"
fi

# ── Step 4: Terraform init & plan ──
echo ""
echo "▶ Step 4: Initializing Terraform..."
cd "$(dirname "$0")/../terraform"

terraform init

echo ""
echo "▶ Step 5: Terraform plan..."
echo "   Review the plan, then run: terraform apply"
echo ""
terraform plan -var-file=terraform.tfvars

echo ""
echo "══════════════════════════════════════════"
echo "  ✅ Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Review the plan above"
echo "  2. Copy terraform.tfvars.example → terraform.tfvars"
echo "  3. Fill in API keys in terraform.tfvars"
echo "  4. Run: cd infra/terraform && terraform apply"
echo "  5. Push Docker images (see deploy instructions)"
echo "══════════════════════════════════════════"

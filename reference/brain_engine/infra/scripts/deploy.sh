#!/bin/bash
# ═══════════════════════════════════════════════════
# Brain Engine — Manual Deploy Script
# Use when not deploying via GitHub Actions
# ═══════════════════════════════════════════════════

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
CLUSTER="brain-engine-prod-cluster"
TAG="${1:-latest}"

echo "══════════════════════════════════════════"
echo "  Deploying Brain Engine to AWS"
echo "  Account: $ACCOUNT_ID | Region: $REGION"
echo "  Tag: $TAG"
echo "══════════════════════════════════════════"

# Login to ECR
echo "▶ Logging into ECR..."
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "$ECR_REGISTRY"

# Build & push backend
echo "▶ Building backend..."
docker build -t "$ECR_REGISTRY/brain-engine-prod-backend:$TAG" .
docker push "$ECR_REGISTRY/brain-engine-prod-backend:$TAG"

# Build & push frontend
echo "▶ Building frontend..."
docker build -t "$ECR_REGISTRY/brain-engine-prod-frontend:$TAG" \
    -f frontend/Dockerfile frontend/
docker push "$ECR_REGISTRY/brain-engine-prod-frontend:$TAG"

# Update ECS services
echo "▶ Updating ECS services..."
aws ecs update-service --cluster "$CLUSTER" \
    --service brain-engine-prod-backend --force-new-deployment --region "$REGION"
aws ecs update-service --cluster "$CLUSTER" \
    --service brain-engine-prod-frontend --force-new-deployment --region "$REGION"

echo "▶ Waiting for deployment to stabilize..."
aws ecs wait services-stable --cluster "$CLUSTER" \
    --services brain-engine-prod-backend brain-engine-prod-frontend --region "$REGION"

ALB_DNS=$(aws elbv2 describe-load-balancers \
    --names brain-engine-prod-alb \
    --query 'LoadBalancers[0].DNSName' --output text --region "$REGION")

echo ""
echo "══════════════════════════════════════════"
echo "  ✅ Deployed successfully!"
echo "  URL: http://$ALB_DNS"
echo "══════════════════════════════════════════"

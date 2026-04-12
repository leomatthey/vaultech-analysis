# Deployment

## SageMaker deployment

Run the deployment script:

```bash
uv run python deploy/deploy_sagemaker.py \
  --bucket vaultech-leo-matthey \
  --region eu-west-1 \
  --endpoint-name vaultech-bath-predictor \
  --model-package-group vaultech-bath-predictor
```

## ECS/Fargate deployment

The Streamlit app is deployed on ECS/Fargate with a public IP. The container
calls the SageMaker endpoint for predictions (no local model needed).

### Build and push

```bash
aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin 808379767763.dkr.ecr.eu-west-1.amazonaws.com
docker build --platform linux/amd64 -t vaultech-streamlit .
docker tag vaultech-streamlit:latest 808379767763.dkr.ecr.eu-west-1.amazonaws.com/vaultech-streamlit:latest
docker push 808379767763.dkr.ecr.eu-west-1.amazonaws.com/vaultech-streamlit:latest
```

### Update ECS service (after pushing new image)

```bash
aws ecs update-service --cluster vaultech-cluster --service vaultech-streamlit-service --force-new-deployment --region eu-west-1
```

## Resource names

| Resource | Name |
|---|---|
| S3 bucket | vaultech-leo-matthey |
| Model Package Group | vaultech-bath-predictor |
| SageMaker endpoint | vaultech-bath-predictor |
| ECR repository | vaultech-streamlit |
| ECS cluster | vaultech-cluster |
| ECS service | vaultech-streamlit-service |
| Task definition | vaultech-streamlit |
| Task execution role | ecsTaskExecutionRole |
| Task role | vaultech-ecs-task-role |
| VPC | vaultech-vpc |
| Security group | vaultech-ecs-sg |
| AWS region | eu-west-1 |
| Public URL | http://108.129.107.16:8501 |

## Validate SageMaker

```bash
export SAGEMAKER_MODEL_PACKAGE_GROUP="vaultech-bath-predictor"
export SAGEMAKER_ENDPOINT_NAME="vaultech-bath-predictor"
export AWS_DEFAULT_REGION="eu-west-1"
uv run pytest tests/test_sagemaker.py -v
```

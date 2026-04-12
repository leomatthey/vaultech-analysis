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

## Resource names

| Resource | Name |
|---|---|
| S3 bucket | vaultech-leo-matthey |
| Model Package Group | vaultech-bath-predictor |
| Endpoint name | vaultech-bath-predictor |
| AWS region | eu-west-1 |

## Validate

```bash
export SAGEMAKER_MODEL_PACKAGE_GROUP="vaultech-bath-predictor"
export SAGEMAKER_ENDPOINT_NAME="vaultech-bath-predictor"
export AWS_DEFAULT_REGION="eu-west-1"
uv run pytest tests/test_sagemaker.py -v
```

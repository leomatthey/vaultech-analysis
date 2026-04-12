"""
SageMaker deployment script — packages, registers, and deploys the XGBoost model.

Usage:
    uv run python deploy/deploy_sagemaker.py \
      --bucket your-bucket-name \
      --region eu-west-1 \
      --endpoint-name your-endpoint-name \
      --model-package-group your-group-name
"""

import argparse
import json
import os
import shutil
import tarfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from sagemaker.core.image_uris import retrieve as retrieve_image_uri


MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_FILE = MODEL_DIR / "xgboost_bath_predictor.ubj"
METADATA_FILE = MODEL_DIR / "model_metadata.json"

# Default execution role — created during project setup.
DEFAULT_ROLE_ARN = (
    "arn:aws:iam::808379767763:role/SageMakerExecutionRole"
)


def package_model(model_path: Path, output_dir: Path) -> Path:
    """Package the XGBoost model as a .tar.gz archive for SageMaker.

    SageMaker's built-in XGBoost container (1.7+) expects a file named
    'xgboost-model' at the root of the archive. We copy the UBJ model
    (binary format compatible with the container) and rename it.

    Args:
        model_path: Path to the trained model UBJ file.
        output_dir: Directory where the .tar.gz will be created.

    Returns:
        Path to the created .tar.gz file.
    """
    tar_path = output_dir / "model.tar.gz"
    tmp_model = output_dir / "xgboost-model"

    # Copy the UBJ model and rename to what SageMaker expects
    shutil.copy2(model_path, tmp_model)

    # Create tar.gz with xgboost-model at the archive root
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(tmp_model, arcname="xgboost-model")

    # Clean up the temporary copy
    tmp_model.unlink()

    return tar_path


def upload_to_s3(local_path: Path, bucket: str, key: str) -> str:
    """Upload a local file to S3.

    Args:
        local_path: Path to the local file.
        bucket: S3 bucket name.
        key: S3 object key.

    Returns:
        Full S3 URI (s3://bucket/key).
    """
    s3 = boto3.client("s3")
    s3.upload_file(str(local_path), bucket, key)
    return f"s3://{bucket}/{key}"


def register_model(
    s3_model_uri: str,
    model_package_group_name: str,
    region: str,
    metrics: dict,
) -> str:
    """Register the model in SageMaker Model Registry.

    Creates the Model Package Group if it doesn't exist, then registers
    a new Model Package version with the XGBoost container image,
    the S3 model artifact, and evaluation metrics.

    Args:
        s3_model_uri: S3 URI of the packaged model (.tar.gz).
        model_package_group_name: Name for the Model Package Group.
        region: AWS region.
        metrics: Dict with 'rmse', 'mae', 'r2' keys.

    Returns:
        The Model Package ARN.
    """
    sm = boto3.client("sagemaker", region_name=region)

    # Create the group if it doesn't exist yet
    try:
        sm.describe_model_package_group(
            ModelPackageGroupName=model_package_group_name
        )
    except ClientError as e:
        if "does not exist" in str(e):
            sm.create_model_package_group(
                ModelPackageGroupName=model_package_group_name,
                ModelPackageGroupDescription=(
                    "XGBoost bath time predictor for forging line pieces"
                ),
            )
        else:
            raise

    # Use sagemaker SDK to retrieve the correct container image URI.
    # Version 3.0-5 matches our local XGBoost 3.2.0 model format.
    image_uri = retrieve_image_uri("xgboost", region, version="3.0-5")

    response = sm.create_model_package(
        ModelPackageGroupName=model_package_group_name,
        ModelPackageDescription="Bath time predictor — XGBoost regressor",
        InferenceSpecification={
            "Containers": [
                {
                    "Image": image_uri,
                    "ModelDataUrl": s3_model_uri,
                }
            ],
            "SupportedContentTypes": ["text/csv"],
            "SupportedResponseMIMETypes": ["text/csv"],
            "SupportedTransformInstanceTypes": ["ml.m5.large"],
            "SupportedRealtimeInferenceInstanceTypes": ["ml.t2.medium"],
        },
        ModelApprovalStatus="Approved",
        CustomerMetadataProperties={
            "rmse": str(metrics["rmse"]),
            "mae": str(metrics["mae"]),
            "r2": str(metrics["r2"]),
        },
    )

    return response["ModelPackageArn"]


def deploy_endpoint(
    model_package_arn: str,
    endpoint_name: str,
    region: str,
    instance_type: str = "ml.t2.medium",
) -> str:
    """Deploy a real-time SageMaker endpoint from a registered Model Package.

    Creates a SageMaker Model, Endpoint Configuration, and Endpoint.
    Waits until the endpoint status is 'InService'.

    Args:
        model_package_arn: ARN of the registered Model Package.
        endpoint_name: Name for the endpoint.
        region: AWS region.
        instance_type: EC2 instance type for the endpoint.

    Returns:
        The endpoint name.
    """
    sm = boto3.client("sagemaker", region_name=region)

    role_arn = os.environ.get("SAGEMAKER_ROLE_ARN", DEFAULT_ROLE_ARN)

    # Create Model from the registered package
    sm.create_model(
        ModelName=endpoint_name,
        PrimaryContainer={"ModelPackageName": model_package_arn},
        ExecutionRoleArn=role_arn,
    )

    # Create Endpoint Configuration
    sm.create_endpoint_config(
        EndpointConfigName=endpoint_name,
        ProductionVariants=[
            {
                "VariantName": "AllTraffic",
                "ModelName": endpoint_name,
                "InstanceType": instance_type,
                "InitialInstanceCount": 1,
            }
        ],
    )

    # Create Endpoint and wait for InService
    sm.create_endpoint(
        EndpointName=endpoint_name,
        EndpointConfigName=endpoint_name,
    )

    print("  Waiting for endpoint to reach InService (this takes ~5-10 min)...")
    waiter = sm.get_waiter("endpoint_in_service")
    waiter.wait(
        EndpointName=endpoint_name,
        WaiterConfig={"Delay": 30, "MaxAttempts": 30},
    )

    return endpoint_name


def test_endpoint(endpoint_name: str, region: str) -> dict:
    """Test the deployed endpoint with sample pieces.

    Invokes the endpoint with representative inputs and compares
    the predictions against expected ranges.

    Args:
        endpoint_name: Name of the deployed endpoint.
        region: AWS region.

    Returns:
        Dict with test results and predictions.
    """
    runtime = boto3.client("sagemaker-runtime", region_name=region)

    test_cases = [
        {"name": "normal_5052", "payload": "5052,18.3,13.5"},
        {"name": "normal_4974", "payload": "4974,17.0,13.0"},
        {"name": "slow_5052", "payload": "5052,30.0,13.5"},
    ]

    results = {}
    for tc in test_cases:
        response = runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="text/csv",
            Body=tc["payload"],
        )
        prediction = float(response["Body"].read().decode("utf-8").strip())
        results[tc["name"]] = {
            "payload": tc["payload"],
            "predicted_bath_time_s": round(prediction, 2),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Deploy XGBoost model to SageMaker")
    parser.add_argument("--bucket", required=True, help="S3 bucket for model artifact")
    parser.add_argument("--region", default="eu-west-1", help="AWS region")
    parser.add_argument("--endpoint-name", required=True, help="SageMaker endpoint name")
    parser.add_argument("--model-package-group", required=True, help="Model Package Group name")
    args = parser.parse_args()

    # Load model metadata for metrics
    with open(METADATA_FILE) as f:
        metadata = json.load(f)

    print("=" * 60)
    print("SageMaker Deployment Pipeline")
    print("=" * 60)

    # Step 1: Package
    print("\n[1/5] Packaging model artifact...")
    tar_path = package_model(MODEL_FILE, MODEL_DIR)
    print(f"  Created: {tar_path}")

    # Step 2: Upload to S3
    print("\n[2/5] Uploading to S3...")
    s3_key = "models/xgboost-bath-predictor/model.tar.gz"
    s3_uri = upload_to_s3(tar_path, args.bucket, s3_key)
    print(f"  Uploaded: {s3_uri}")

    # Step 3: Register in Model Registry
    print("\n[3/5] Registering in Model Registry...")
    model_package_arn = register_model(
        s3_uri, args.model_package_group, args.region, metadata["metrics"]
    )
    print(f"  Registered: {model_package_arn}")

    # Step 4: Deploy endpoint
    print("\n[4/5] Deploying endpoint...")
    endpoint = deploy_endpoint(model_package_arn, args.endpoint_name, args.region)
    print(f"  Endpoint live: {endpoint}")

    # Step 5: Test
    print("\n[5/5] Testing endpoint...")
    results = test_endpoint(args.endpoint_name, args.region)
    print(f"  Results: {json.dumps(results, indent=2)}")

    print("\n" + "=" * 60)
    print("Deployment complete!")
    print(f"  Endpoint:       {args.endpoint_name}")
    print(f"  Model Package:  {model_package_arn}")
    print(f"  S3 artifact:    {s3_uri}")
    print("=" * 60)


if __name__ == "__main__":
    main()

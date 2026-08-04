"""Helpers for fetching document bytes from Amazon S3."""
from __future__ import annotations

import os
from urllib.parse import urlparse

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".markdown", ".text")


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` and return (bucket, key)."""
    raw = (s3_uri or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "s3":
        raise ValueError("S3 URI must start with s3://")
    bucket = parsed.netloc.strip()
    key = parsed.path.lstrip("/").strip()
    if not bucket or not key:
        raise ValueError("S3 URI must include both bucket and object key (s3://bucket/key).")
    return bucket, key


def _resolve_region(region: str | None) -> str | None:
    if region and region.strip():
        return region.strip()
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _create_s3_client(region: str | None):
    import boto3

    session_kwargs: dict[str, str] = {}
    resolved_region = _resolve_region(region)
    profile = os.environ.get("AWS_PROFILE")
    if profile:
        session_kwargs["profile_name"] = profile
    if resolved_region:
        session_kwargs["region_name"] = resolved_region
    session = boto3.Session(**session_kwargs)
    return session.client("s3")


def fetch_s3_document(s3_uri: str, region: str | None = None) -> tuple[str, bytes]:
    """Download one S3 object and return ``(filename, bytes)``."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("boto3 is not installed. Add boto3 to requirements and install dependencies.") from exc

    bucket, key = parse_s3_uri(s3_uri)
    client = _create_s3_client(region)

    try:
        response = client.get_object(Bucket=bucket, Key=key)
        data: bytes = response["Body"].read()
    except NoCredentialsError as exc:
        raise RuntimeError(
            "AWS credentials not found. Configure credentials via aws configure, environment variables, or AWS_PROFILE."
        ) from exc
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        raise RuntimeError(f"Failed to fetch '{s3_uri}' from S3 ({error_code}).") from exc
    except BotoCoreError as exc:
        raise RuntimeError(f"AWS SDK error while fetching '{s3_uri}': {exc}") from exc

    if not data:
        raise RuntimeError(f"S3 object '{s3_uri}' is empty.")

    filename = key.rsplit("/", 1)[-1] or "s3-object.txt"
    return filename, data


def fetch_s3_documents(s3_uri: str, region: str | None = None) -> list[tuple[str, str, bytes]]:
    """Download all supported documents under an S3 prefix.

    Returns a list of ``(object_uri, filename, bytes)``.
    """
    try:
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("boto3 is not installed. Add boto3 to requirements and install dependencies.") from exc

    bucket, prefix = parse_s3_uri(s3_uri)
    if not prefix.endswith("/"):
        raise ValueError("S3 folder mode requires a prefix ending with '/'.")

    client = _create_s3_client(region)
    keys: list[str] = []
    continuation_token: str | None = None
    try:
        while True:
            request_kwargs = {"Bucket": bucket, "Prefix": prefix}
            if continuation_token:
                request_kwargs["ContinuationToken"] = continuation_token
            response = client.list_objects_v2(**request_kwargs)
            contents = response.get("Contents", [])
            for obj in contents:
                key = obj.get("Key", "")
                if key.endswith("/"):
                    continue
                if key.lower().endswith(SUPPORTED_EXTENSIONS):
                    keys.append(key)
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
    except NoCredentialsError as exc:
        raise RuntimeError(
            "AWS credentials not found. Configure credentials via aws configure, environment variables, or AWS_PROFILE."
        ) from exc
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        raise RuntimeError(f"Failed to list '{s3_uri}' from S3 ({error_code}).") from exc
    except BotoCoreError as exc:
        raise RuntimeError(f"AWS SDK error while listing '{s3_uri}': {exc}") from exc

    if not keys:
        raise RuntimeError(
            f"No supported documents found under '{s3_uri}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    documents: list[tuple[str, str, bytes]] = []
    for key in keys:
        object_uri = f"s3://{bucket}/{key}"
        filename = key.rsplit("/", 1)[-1] or "s3-object.txt"
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            data: bytes = response["Body"].read()
        except NoCredentialsError as exc:
            raise RuntimeError(
                "AWS credentials not found. Configure credentials via aws configure, environment variables, or AWS_PROFILE."
            ) from exc
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(f"Failed to fetch '{object_uri}' from S3 ({error_code}).") from exc
        except BotoCoreError as exc:
            raise RuntimeError(f"AWS SDK error while fetching '{object_uri}': {exc}") from exc

        if data:
            documents.append((object_uri, filename, data))

    if not documents:
        raise RuntimeError(f"All supported objects under '{s3_uri}' were empty.")

    return documents

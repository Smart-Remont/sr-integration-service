from __future__ import annotations

import mimetypes

import aioboto3
from botocore.config import Config
from loguru import logger

from src.config import minio_config


def detect_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def put_object(*, key: str, body: bytes, content_type: str | None = None) -> None:
    normalized_key = key.lstrip("/")
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio_config.endpoint,
        aws_access_key_id=minio_config.access_key,
        aws_secret_access_key=minio_config.secret_key,
        region_name=minio_config.region,
        config=Config(signature_version="s3v4"),
    ) as client:
        await client.put_object(
            Bucket=minio_config.bucket,
            Key=normalized_key,
            Body=body,
            ContentType=content_type or "application/octet-stream",
        )
    logger.info(
        "minio put_object bucket={} key={} bytes={}",
        minio_config.bucket,
        normalized_key,
        len(body),
    )


async def get_object(key: str) -> bytes:
    normalized_key = key.lstrip("/")
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio_config.endpoint,
        aws_access_key_id=minio_config.access_key,
        aws_secret_access_key=minio_config.secret_key,
        region_name=minio_config.region,
        config=Config(signature_version="s3v4"),
    ) as client:
        response = await client.get_object(Bucket=minio_config.bucket, Key=normalized_key)
        async with response["Body"] as body:
            return await body.read()

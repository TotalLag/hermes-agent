"""
MinIO MCP Server - Object storage via MinIO/S3-compatible API.

Provides tools for listing buckets, listing/putting/getting objects,
and generating presigned URLs for secure direct access.

Requires environment variables:
    MINIO_ENDPOINT     Host:port of MinIO server (default: localhost:9000)
    MINIO_ACCESS_KEY  MinIO access key (or set via MINIO_ROOT_USER)
    MINIO_SECRET_KEY  MinIO secret key (or set via MINIO_ROOT_PASSWORD)
    MINIO_BUCKET      Default bucket name for operations (optional)
    MINIO_SECURE      Set "true" for HTTPS (default: false)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

_server: Optional[Minio] = None
_endpoint: str = ""
_bucket: str = ""


def _get_client() -> Minio:
    """Get or create the MinIO client (lazy init)."""
    global _server
    if _server is None:
        raise RuntimeError(
            "MinIO MCP server not initialized. Call minio_initialize first."
        )
    return _server


def _load_config() -> dict:
    """Load MinIO configuration from environment variables."""
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER", "")
    secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD", "")
    default_bucket = os.getenv("MINIO_BUCKET", "")
    secure = os.getenv("MINIO_SECURE", "").lower() in ("true", "1", "yes")

    if not access_key or not secret_key:
        raise ValueError(
            "MinIO credentials not set. Set MINIO_ACCESS_KEY and "
            "MINIO_SECRET_KEY (or MINIO_ROOT_USER / MINIO_ROOT_PASSWORD)."
        )

    return {
        "endpoint": endpoint,
        "access_key": access_key,
        "secret_key": secret_key,
        "default_bucket": default_bucket,
        "secure": secure,
    }


TOOL_DEFINITIONS = [
    {
        "name": "minio_initialize",
        "description": "Initialize MinIO client and validate connectivity. "
        "Must be called before any other MinIO tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "MinIO server endpoint (host:port). "
                    "Defaults to MINIO_ENDPOINT env var.",
                },
                "access_key": {
                    "type": "string",
                    "description": "MinIO access key. "
                    "Defaults to MINIO_ACCESS_KEY env var.",
                },
                "secret_key": {
                    "type": "string",
                    "description": "MinIO secret key. "
                    "Defaults to MINIO_SECRET_KEY env var.",
                },
                "default_bucket": {
                    "type": "string",
                    "description": "Default bucket name for operations. "
                    "Defaults to MINIO_BUCKET env var.",
                },
                "secure": {
                    "type": "boolean",
                    "description": "Use HTTPS. Defaults to false.",
                },
            },
        },
    },
    {
        "name": "minio_list_buckets",
        "description": "List all buckets accessible by the current credentials.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "minio_list_objects",
        "description": "List objects in a bucket, optionally filtered by prefix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "description": "Bucket name. Defaults to the default bucket.",
                },
                "prefix": {
                    "type": "string",
                    "description": "Filter objects whose keys start with this prefix.",
                },
                "max_keys": {
                    "type": "integer",
                    "description": "Maximum number of objects to return (default 1000).",
                    "default": 1000,
                },
            },
        },
    },
    {
        "name": "minio_get_object",
        "description": "Get an object's metadata and a presigned download URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "description": "Bucket name. Defaults to the default bucket.",
                },
                "key": {
                    "type": "string",
                    "description": "Object key (path within bucket).",
                },
                "expires_seconds": {
                    "type": "integer",
                    "description": "Presigned URL expiry in seconds (default 3600, max 604800).",
                    "default": 3600,
                },
            },
            "required": ["key"],
        },
    },
    {
        "name": "minio_put_object",
        "description": "Get a presigned upload URL for putting an object into MinIO. "
        "Returns a presigned PUT URL that can be used with HTTP PUT to upload data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "description": "Bucket name. Defaults to the default bucket.",
                },
                "key": {
                    "type": "string",
                    "description": "Object key (path within bucket).",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type of the object (default: application/octet-stream).",
                    "default": "application/octet-stream",
                },
                "expires_seconds": {
                    "type": "integer",
                    "description": "Presigned URL expiry in seconds (default 3600, max 604800).",
                    "default": 3600,
                },
            },
            "required": ["key"],
        },
    },
    {
        "name": "minio_health",
        "description": "Health check - verify MinIO is reachable and credentials are valid.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "description": "Bucket name to check existence for (optional).",
                },
            },
        },
    },
]


def minio_initialize(
    endpoint: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    default_bucket: Optional[str] = None,
    secure: Optional[bool] = None,
) -> str:
    """Initialize MinIO client. Stores config for subsequent calls."""
    global _server, _endpoint, _bucket

    config = _load_config()

    if endpoint:
        config["endpoint"] = endpoint
    if access_key:
        config["access_key"] = access_key
    if secret_key:
        config["secret_key"] = secret_key
    if default_bucket:
        config["default_bucket"] = default_bucket
    if secure is not None:
        config["secure"] = secure

    _endpoint = config["endpoint"]
    _bucket = config["default_bucket"]

    # Create client
    _server = Minio(
        endpoint=config["endpoint"],
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        secure=config["secure"],
    )

    # Validate connectivity with a list_buckets call
    try:
        buckets = _server.list_buckets()
        bucket_names = [b.name for b in buckets]
        logger.info(
            "MinIO initialized: endpoint=%s, buckets=%s",
            config["endpoint"],
            bucket_names,
        )
        return json.dumps(
            {
                "status": "initialized",
                "endpoint": config["endpoint"],
                "buckets": bucket_names,
                "default_bucket": config["default_bucket"] or "(none)",
            }
        )
    except S3Error as e:
        _server = None
        logger.error("MinIO initialization failed: %s", e)
        return json.dumps(
            {
                "status": "error",
                "message": f"Failed to connect to MinIO at {config['endpoint']}: {e.message}",
            }
        )


def minio_list_buckets() -> str:
    """List all buckets."""
    client = _get_client()
    try:
        buckets = client.list_buckets()
        result = [
            {
                "name": b.name,
                "creation_date": b.creation_date.isoformat()
                if b.creation_date
                else None,
            }
            for b in buckets
        ]
        return json.dumps({"status": "ok", "buckets": result})
    except S3Error as e:
        logger.error("minio_list_buckets failed: %s", e)
        return json.dumps({"status": "error", "message": e.message})


def minio_list_objects(
    bucket: Optional[str] = None,
    prefix: str = "",
    max_keys: int = 1000,
) -> str:
    """List objects in a bucket."""
    client = _get_client()
    bucket = bucket or _bucket
    if not bucket:
        return json.dumps(
            {
                "status": "error",
                "message": "No bucket specified and MINIO_BUCKET is not set.",
            }
        )

    try:
        objects = client.list_objects(bucket, prefix=prefix, max_keys=max_keys)
        result = []
        for obj in objects:
            result.append(
                {
                    "key": obj.object_name,
                    "size": obj.size,
                    "last_modified": (
                        obj.last_modified.isoformat() if obj.last_modified else None
                    ),
                    "etag": obj.etag,
                    "is_dir": obj.is_dir,
                }
            )
        return json.dumps(
            {"status": "ok", "bucket": bucket, "prefix": prefix, "objects": result}
        )
    except S3Error as e:
        logger.error("minio_list_objects failed: %s", e)
        return json.dumps({"status": "error", "message": e.message})


def minio_get_object(
    bucket: Optional[str] = None,
    key: str = "",
    expires_seconds: int = 3600,
) -> str:
    """Get an object's metadata and a presigned download URL."""
    client = _get_client()
    bucket = bucket or _bucket
    if not bucket:
        return json.dumps(
            {
                "status": "error",
                "message": "No bucket specified and MINIO_BUCKET is not set.",
            }
        )
    if not key:
        return json.dumps({"status": "error", "message": "key is required."})

    # Cap expiry to 7 days (S3 limit)
    expires_seconds = min(expires_seconds, 604800)

    try:
        # Check object exists by trying to get its stat
        stat = client.stat_object(bucket, key)
        url = client.presigned_get_object(bucket, key, expires=expires_seconds)
        return json.dumps(
            {
                "status": "ok",
                "bucket": bucket,
                "key": key,
                "size": stat.size,
                "last_modified": (
                    stat.last_modified.isoformat() if stat.last_modified else None
                ),
                "etag": stat.etag,
                "content_type": stat.content_type,
                "presigned_url": url,
                "expires_in_seconds": expires_seconds,
            }
        )
    except S3Error as e:
        if e.code == "NoSuchKey":
            return json.dumps(
                {"status": "error", "message": f"Object not found: {key}"}
            )
        logger.error("minio_get_object failed: %s", e)
        return json.dumps({"status": "error", "message": e.message})


def minio_put_object(
    bucket: Optional[str] = None,
    key: str = "",
    content_type: str = "application/octet-stream",
    expires_seconds: int = 3600,
) -> str:
    """Get a presigned URL for uploading an object."""
    client = _get_client()
    bucket = bucket or _bucket
    if not bucket:
        return json.dumps(
            {
                "status": "error",
                "message": "No bucket specified and MINIO_BUCKET is not set.",
            }
        )
    if not key:
        return json.dumps({"status": "error", "message": "key is required."})

    # Cap expiry to 7 days
    expires_seconds = min(expires_seconds, 604800)

    try:
        url = client.presigned_put_object(bucket, key, expires=expires_seconds)
        return json.dumps(
            {
                "status": "ok",
                "bucket": bucket,
                "key": key,
                "content_type": content_type,
                "presigned_url": url,
                "expires_in_seconds": expires_seconds,
                "instructions": (
                    f"HTTP PUT to the presigned_url with the file body. "
                    f"Set Content-Type header to '{content_type}'."
                ),
            }
        )
    except S3Error as e:
        logger.error("minio_put_object failed: %s", e)
        return json.dumps({"status": "error", "message": e.message})


def minio_health(bucket: Optional[str] = None) -> str:
    """Health check - verify MinIO is reachable."""
    client = _get_client()
    bucket = bucket or _bucket

    try:
        # List buckets as a connectivity check
        buckets = client.list_buckets()
        bucket_names = [b.name for b in buckets]

        if bucket:
            exists = client.bucket_exists(bucket)
            return json.dumps(
                {
                    "status": "healthy",
                    "endpoint": _endpoint,
                    "buckets": bucket_names,
                    "default_bucket": bucket,
                    "default_bucket_exists": exists,
                }
            )
        else:
            return json.dumps(
                {
                    "status": "healthy",
                    "endpoint": _endpoint,
                    "buckets": bucket_names,
                    "default_bucket": _bucket or "(not set)",
                }
            )
    except S3Error as e:
        logger.error("minio_health failed: %s", e)
        return json.dumps(
            {
                "status": "unhealthy",
                "endpoint": _endpoint,
                "error": e.message,
            }
        )
    except Exception as e:
        logger.exception("Tool %s raised: %s", tool_name, e)
        return json.dumps({"status": "error", "message": str(e)})


TOOL_HANDLERS = {
    "minio_initialize": minio_initialize,
    "minio_list_buckets": minio_list_buckets,
    "minio_list_objects": minio_list_objects,
    "minio_get_object": minio_get_object,
    "minio_put_object": minio_put_object,
    "minio_health": minio_health,
}


def handle_tool_call(tool_name: str, arguments: dict) -> str:
    """Main entry point for the MCP server."""
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return json.dumps({"status": "error", "message": f"Unknown tool: {tool_name}"})

    try:
        return handler(**arguments)
    except TypeError as e:
        # Missing required argument
        return json.dumps({"status": "error", "message": f"Missing argument: {e}"})
    except RuntimeError as e:
        # Not initialized
        return json.dumps({"status": "error", "message": str(e)})
    except Exception as e:
        logger.exception("Tool %s raised: %s", tool_name, e)
        return json.dumps({"status": "error", "message": str(e)})


if __name__ == "__main__":
    try:
        from gateway.hiclaw.logging_config import setup_mcp_logging

        setup_mcp_logging(__name__)
    except ImportError:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            tool_name = request.get("name", "")
            arguments = request.get("arguments", {})
            result = handle_tool_call(tool_name, arguments)
            print(result)
        except json.JSONDecodeError:
            print(json.dumps({"status": "error", "message": "Invalid JSON"}))

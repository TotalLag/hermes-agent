#!/usr/bin/env python3
"""
hermes-hiclaw — HiClaw worker/manager CLI for Hermes agent.

Handles worker self-registration and heartbeat via Matrix DMs to the Manager,
and manager initialization.

Usage:
    hermes-hiclaw worker register    Register this worker with the Manager
    hermes-hiclaw worker heartbeat  Run heartbeat loop (daemon)
    hermes-hiclaw manager init       Initialize Manager config

Environment variables:
    HICLAW_WORKER_NAME         Unique worker name (e.g. copilot-01)
    HICLAW_WORKER_VERSION       Worker version string
    HICLAW_MANAGER_MXID        Manager's Matrix user ID
    HICLAW_MANAGER_ROOM_ID     Manager's Matrix room ID
    HICLAW_REGISTRATION_TOKEN   Token passed during registration
    MATRIX_HOMESERVER           Matrix homeserver URL
    MATRIX_ACCESS_TOKEN         This worker's Matrix access token
    HICLAW_MINIO_ENDPOINT      MinIO endpoint (for worker info)
    HICLAW_MINIO_BUCKET         MinIO bucket
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hermes-hiclaw")


async def send_matrix_dm(
    homeserver: str, token: str, user_id: str, room_id: str, message: str
) -> bool:
    try:
        from nio import AsyncClient, LoginResponse

        user, server = (
            user_id.split(":", 1) if ":" in user_id else (user_id, homeserver)
        )
        client = AsyncClient(homeserver, user)
        resp = await client.login(token)
        if not isinstance(resp, LoginResponse):
            logger.error("Login failed: %s", resp)
            return False
        await client.room_send(
            room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": message,
            },
        )
        await client.close()
        return True
    except Exception as e:
        logger.error("Failed to send Matrix DM: %s", e)
        return False


def get_worker_info() -> dict:
    import socket
    import uuid as uuid_lib

    return {
        "id": os.getenv("HICLAW_WORKER_ID") or str(uuid_lib.uuid4()),
        "name": os.getenv("HICLAW_WORKER_NAME") or socket.gethostname(),
        "capabilities": ["terminal", "web", "code"],
        "status": "ready",
        "version": os.getenv("HICLAW_WORKER_VERSION", "1.0.0"),
        "matrix_user_id": os.getenv("MATRIX_USER_ID", ""),
        "device_id": os.getenv("HERMES_DEVICE_ID", ""),
        "registered_at": datetime.utcnow().isoformat() + "Z",
        "last_seen_at": datetime.utcnow().isoformat() + "Z",
        "room_id": os.getenv("HICLAW_WORKER_ROOM_ID", ""),
        "metadata": {
            "minio_endpoint": os.getenv("HICLAW_MINIO_ENDPOINT", ""),
            "minio_bucket": os.getenv("HICLAW_MINIO_BUCKET", ""),
        },
    }


def cmd_worker_register(args: argparse.Namespace) -> int:
    token = os.getenv("MATRIX_ACCESS_TOKEN", "")
    homeserver = os.getenv("MATRIX_HOMESERVER", "")
    manager_mxid = os.getenv("HICLAW_MANAGER_MXID", "")
    manager_room = os.getenv("HICLAW_MANAGER_ROOM_ID", "")
    registration_token = os.getenv("HICLAW_REGISTRATION_TOKEN", "")

    if not all([token, homeserver, manager_mxid, manager_room]):
        logger.error(
            "Missing required env vars. Need: MATRIX_ACCESS_TOKEN, MATRIX_HOMESERVER, "
            "HICLAW_MANAGER_MXID, HICLAW_MANAGER_ROOM_ID"
        )
        return 1

    worker_info = get_worker_info()
    worker_info["registration_token"] = registration_token
    message = json.dumps({"type": "worker.register", "payload": worker_info}, indent=2)

    logger.info(
        "Registering worker %s with Manager %s...", worker_info["name"], manager_mxid
    )
    ok = asyncio.run(
        send_matrix_dm(
            homeserver, token, worker_info["matrix_user_id"], manager_room, message
        )
    )
    if ok:
        logger.info("Worker registered successfully")
        state_dir = Path.home() / ".hermes" / "hiclaw"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "worker-id").write_text(worker_info["id"])
        return 0
    return 1


def cmd_worker_heartbeat(args: argparse.Namespace) -> int:
    token = os.getenv("MATRIX_ACCESS_TOKEN", "")
    homeserver = os.getenv("MATRIX_HOMESERVER", "")
    worker_mxid = os.getenv("MATRIX_USER_ID", "")
    manager_room = os.getenv("HICLAW_MANAGER_ROOM_ID", "")
    interval = int(os.getenv("HICLAW_HEARTBEAT_INTERVAL", "120"))

    if not all([token, homeserver, worker_mxid, manager_room]):
        logger.error(
            "Missing required env vars. Need: MATRIX_ACCESS_TOKEN, MATRIX_HOMESERVER, "
            "MATRIX_USER_ID, HICLAW_MANAGER_ROOM_ID"
        )
        return 1

    state_dir = Path.home() / ".hermes" / "hiclaw"
    worker_id = (
        (state_dir / "worker-id").read_text().strip()
        if (state_dir / "worker-id").exists()
        else "unknown"
    )

    logger.info("Starting heartbeat loop (interval=%ds)", interval)
    while True:
        payload = {
            "type": "worker.heartbeat",
            "worker_id": worker_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": "ready",
        }
        message = json.dumps(payload)
        ok = asyncio.run(
            send_matrix_dm(homeserver, token, worker_mxid, manager_room, message)
        )
        if not ok:
            logger.warning("Heartbeat failed, retrying in 30s...")
            time.sleep(30)
        else:
            logger.debug("Heartbeat sent at %s", payload["timestamp"])
        time.sleep(interval)


def cmd_manager_init(args: argparse.Namespace) -> int:
    workspace_dir = Path(
        args.workspace or os.getenv("HICLAW_WORKSPACE_DIR", "/root/manager-workspace")
    )
    import secrets

    gateway_key = secrets.token_urlsafe(32)
    manager_password = secrets.token_urlsafe(24)

    config = {
        "HICLAW_MANAGER_GATEWAY_KEY": gateway_key,
        "HICLAW_MANAGER_PASSWORD": manager_password,
    }

    env_file = workspace_dir / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip().strip('"').strip("'")

    existing.update(config)
    content = "\n".join(f"{k}={v}" for k, v in sorted(existing.items())) + "\n"
    env_file.write_text(content)
    env_file.chmod(0o600)
    logger.info("Manager config written to %s", env_file)
    logger.info("Gateway key: %s", gateway_key)
    logger.info("Manager password: %s", manager_password)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="hermes-hiclaw", description=__doc__)
    sub = parser.add_subparsers(required=True)

    w = sub.add_parser("worker", help="Worker commands")
    w_sub = w.add_subparsers(required=True)
    w_reg = w_sub.add_parser("register", help="Register this worker with the Manager")
    w_hb = w_sub.add_parser("heartbeat", help="Run worker heartbeat loop (daemon)")
    w.set_defaults(func=lambda a: 0)

    w_reg.set_defaults(func=cmd_worker_register)
    w_hb.set_defaults(func=cmd_worker_heartbeat)

    m = sub.add_parser("manager", help="Manager commands")
    m_sub = m.add_subparsers(required=True)
    m_init = m_sub.add_parser("init", help="Initialize Manager config and secrets")
    m_init.add_argument("--workspace", help="Workspace directory")
    m_init.set_defaults(func=cmd_manager_init)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Bootstrap Matrix users for HiClaw local development.

Registers manager, worker, and admin users against the local Synapse homeserver
and prints their access tokens for use in docker/hiclaw.env.

Usage:
    python docker/bootstrap-matrix-users.py

Requires:
    pip install requests
"""

import json
import sys
import uuid
import hashlib

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)


def get_nonce(synapse_url: str, shared_secret: str) -> str:
    resp = requests.get(
        f"{synapse_url}/_synapse/admin/v1/register",
        params={"shared_secret": shared_secret},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["nonce"]


def register_user(
    synapse_url: str,
    shared_secret: str,
    username: str,
    password: str,
    admin: bool = False,
    bot: bool = False,
) -> dict:
    nonce = get_nonce(synapse_url, shared_secret)
    body = {
        "nonce": nonce,
        "username": username,
        "password": password,
        "admin": admin,
        "bot": bot,
        "displayname": username,
    }
    auth = hashlib.sha256(f"{nonce}{shared_secret}".encode()).hexdigest()
    resp = requests.post(
        f"{synapse_url}/_synapse/admin/v1/register",
        json=body,
        headers={"Authorization": f"Matrix {auth}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_access_token(synapse_url: str, username: str, password: str) -> str:
    resp = requests.post(
        f"{synapse_url}/_matrix/client/r0/login",
        json={
            "identifier": {"type": "m.id.user", "user": username},
            "password": password,
            "type": "m.login.password",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def main():
    import os

    synapse_url = os.getenv("SYNAPSE_URL", "http://localhost:8008")
    shared_secret = os.getenv(
        "SYNAPSE_REGISTRATION_SHARED_SECRET",
        os.getenv("SYNAPSE_SHARED_SECRET", ""),
    )
    if not shared_secret:
        print("Error: SYNAPSE_REGISTRATION_SHARED_SECRET env var not set")
        sys.exit(1)

    users = [
        (
            "hermes-manager",
            os.getenv("HICLAW_MANAGER_PASSWORD", "manager_password"),
            False,
        ),
        (
            "hermes-worker-1",
            os.getenv("HICLAW_WORKER1_PASSWORD", "worker1_password"),
            False,
        ),
        ("admin", os.getenv("HICLAW_ADMIN_PASSWORD", "admin_password"), True),
    ]

    print(f"Connecting to Synapse at {synapse_url}")
    results = {}
    for username, password, is_admin in users:
        full_user = f"@{username}:hermes.local"
        print(f"\nRegistering {full_user} (admin={is_admin})...")
        result = register_user(
            synapse_url,
            shared_secret,
            username,
            password,
            admin=is_admin,
        )
        user_id = result.get("user_id", full_user)
        print(f"  Registered user_id: {user_id}")

        print(f"  Getting access token for {full_user}...")
        token = get_access_token(synapse_url, username, password)
        print(f"  Access token: {token[:20]}...")
        results[username] = {
            "user_id": user_id,
            "access_token": token,
        }

    print("\n\n=== Add these to docker/hiclaw.env ===\n")
    for username, data in results.items():
        token = data["access_token"]
        mxid = data["user_id"]
        if username == "hermes-manager":
            print(f"HICLAW_MANAGER_MXID={mxid}")
            print(f"HICLAW_MANAGER_ACCESS_TOKEN={token}")
        elif username == "hermes-worker-1":
            print(f"HICLAW_WORKER1_MXID={mxid}")
            print(f"HICLAW_WORKER1_ACCESS_TOKEN={token}")
        elif username == "admin":
            print(f"HICLAW_ADMIN_MXID={mxid}")
        print()


if __name__ == "__main__":
    main()

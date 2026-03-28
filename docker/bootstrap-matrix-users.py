#!/usr/bin/env python3
"""
Bootstrap Matrix users for HiClaw local development.

Registers manager, worker, and admin users against the local Synapse homeserver
and prints their access tokens for use in docker/hiclaw.env.

Usage:
    python docker/bootstrap-matrix-users.py
    python docker/bootstrap-matrix-users.py --check  # Just check if users exist

Requires:
    pip install requests
"""

import os
import sys
import json
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


def user_exists(synapse_url: str, username: str, admin_token: str) -> bool:
    """Check if a user already exists via admin API."""
    resp = requests.get(
        f"{synapse_url}/_synapse/admin/v1/users/{username}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    return resp.status_code == 200


def get_admin_token(
    synapse_url: str, shared_secret: str, username: str, password: str
) -> str:
    """Get admin token by registering or logging in the admin user."""
    # First try to register (will fail if already exists)
    try:
        result = register_user(
            synapse_url,
            shared_secret,
            username,
            password,
            admin=True,
        )
        return result.get("access_token", "")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            # User already exists, get token via login
            return get_access_token(synapse_url, username, password)
        raise


def ensure_user(
    synapse_url: str,
    username: str,
    password: str,
    admin_token: str,
    shared_secret: str,
    is_admin: bool = False,
) -> tuple[str, str]:
    """
    Ensure a user exists and return (user_id, access_token).
    If user exists, get token via login. If not, register then login.
    """
    if user_exists(synapse_url, username, admin_token):
        # User exists, get token via login
        token = get_access_token(synapse_url, username, password)
        # Get the user_id from whoami
        resp = requests.get(
            f"{synapse_url}/_matrix/client/r0/account/whoami",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        user_id = resp.json().get("user_id", f"@{username}:hermes.local")
        return user_id, token
    else:
        # User doesn't exist, register them
        if shared_secret:
            result = register_user(
                synapse_url,
                shared_secret,
                username,
                password,
                admin=is_admin,
            )
        else:
            # No shared secret, can't register - should not happen in practice
            raise RuntimeError(
                f"User {username} does not exist and no shared secret to register new users"
            )
        user_id = result.get("user_id", f"@{username}:hermes.local")
        token = get_access_token(synapse_url, username, password)
        return user_id, token


def main():
    synapse_url = os.getenv("SYNAPSE_URL", "http://localhost:8008")
    shared_secret = os.getenv(
        "SYNAPSE_REGISTRATION_SHARED_SECRET",
        os.getenv("SYNAPSE_SHARED_SECRET", ""),
    )
    admin_token = os.getenv("SYNAPSE_ADMIN_TOKEN", "")

    # Determine worker count (default 1, max 10)
    worker_count_str = os.getenv("HICLAW_WORKER_COUNT", "1")
    try:
        worker_count = min(max(int(worker_count_str), 1), 10)
    except ValueError:
        worker_count = 1

    # Build worker list
    workers = []
    for i in range(1, worker_count + 1):
        workers.append(
            {
                "name": f"hermes-worker-{i}",
                "env_mxid": f"HICLAW_WORKER{i}_MXID",
                "env_password": f"HICLAW_WORKER{i}_PASSWORD",
                "env_token": f"HICLAW_WORKER{i}_ACCESS_TOKEN",
            }
        )

    # Build full user list: admin, manager, then workers
    users = [
        (
            "admin",
            os.getenv("HICLAW_ADMIN_PASSWORD", "admin_password"),
            True,
        ),
        (
            "hermes-manager",
            os.getenv("HICLAW_MANAGER_PASSWORD", "manager_password"),
            False,
        ),
    ]
    for worker in workers:
        users.append(
            (
                worker["name"],
                os.getenv(worker["env_password"], f"{worker['name']}_password"),
                False,
            )
        )

    # --check mode: just verify existence
    if "--check" in sys.argv:
        if not admin_token:
            print("Error: SYNAPSE_ADMIN_TOKEN env var required for --check mode")
            sys.exit(1)
        print(f"Checking users on Synapse at {synapse_url}\n")
        all_exist = True
        for username, password, is_admin in users:
            if user_exists(synapse_url, username, admin_token):
                print(f"  {username}: EXISTS")
            else:
                print(f"  {username}: MISSING")
                all_exist = False
        sys.exit(0 if all_exist else 1)

    print(f"Connecting to Synapse at {synapse_url}")

    # First, ensure admin user exists and get admin token
    admin_username = "admin"
    admin_password = os.getenv("HICLAW_ADMIN_PASSWORD", "admin_password")
    admin_mxid = f"@{admin_username}:hermes.local"
    admin_access_token = ""

    if admin_token:
        # Use provided admin token
        if user_exists(synapse_url, admin_username, admin_token):
            admin_access_token = admin_token
            print(f"\nUsing existing admin token from SYNAPSE_ADMIN_TOKEN")
        else:
            # Token might be invalid or user doesn't exist, try login
            try:
                admin_access_token = get_access_token(
                    synapse_url, admin_username, admin_password
                )
                print(f"\nValidated admin token via login")
            except requests.exceptions.HTTPError:
                print(f"\nAdmin token invalid and admin user may not exist")
    elif shared_secret:
        # Get or create admin token using shared secret
        admin_access_token = get_admin_token(
            synapse_url, shared_secret, admin_username, admin_password
        )
        print(f"\nAdmin user initialized with token")

    if not admin_access_token:
        print(
            "Error: Could not obtain admin token. Set SYNAPSE_ADMIN_TOKEN or SYNAPSE_REGISTRATION_SHARED_SECRET"
        )
        sys.exit(1)

    # Now ensure all other users exist
    results = {}
    for username, password, is_admin in users:
        if username == "admin":
            # Admin already handled
            full_user = admin_mxid
            token = admin_access_token
            results[username] = {
                "user_id": admin_mxid,
                "access_token": token,
            }
            print(f"\nAdmin user: {admin_mxid}")
            print(f"  (token already obtained)")
            continue

        full_user = f"@{username}:hermes.local"
        print(f"\nEnsuring {full_user}...")

        user_id, token = ensure_user(
            synapse_url,
            username,
            password,
            admin_access_token,
            shared_secret,
            is_admin=is_admin,
        )

        print(f"  user_id: {user_id}")
        print(f"  access_token: {token[:20]}...")
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
        elif username.startswith("hermes-worker-"):
            # Extract worker number
            worker_num = username.split("-")[-1]
            print(f"HICLAW_WORKER{worker_num}_MXID={mxid}")
            print(f"HICLAW_WORKER{worker_num}_ACCESS_TOKEN={token}")
        elif username == "admin":
            print(f"HICLAW_ADMIN_MXID={mxid}")
        print()


if __name__ == "__main__":
    main()

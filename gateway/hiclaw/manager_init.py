import json
import logging
import os
import secrets as py_secrets
import shutil
import socket
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _matrix_server_url(matrix_domain: str) -> str:
    server = os.getenv("HICLAW_MATRIX_SERVER")
    if server:
        return server.rstrip("/")
    return f"http://{matrix_domain}".rstrip("/")


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    content = (
        f"HICLAW_MANAGER_GATEWAY_KEY={values['HICLAW_MANAGER_GATEWAY_KEY']}\n"
        f"HICLAW_MANAGER_PASSWORD={values['HICLAW_MANAGER_PASSWORD']}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _register_matrix_user(
    client: httpx.Client,
    matrix_server: str,
    username: str,
    password: str,
    registration_token: str | None,
) -> bool:
    payload: dict[str, Any] = {
        "username": username,
        "password": password,
    }
    if registration_token:
        payload["auth"] = {
            "type": "m.login.registration_token",
            "token": registration_token,
        }

    try:
        response = client.post(
            f"{matrix_server}/_matrix/client/v3/register",
            json=payload,
        )
    except httpx.HTTPError as exc:
        logger.error("Matrix register failed for %s: %s", username, exc)
        return False

    if response.status_code in (200, 201):
        logger.info("Matrix user registered: %s", username)
        return True

    body = response.text
    if response.status_code in (400, 409) and (
        "M_USER_IN_USE" in body or "already" in body.lower()
    ):
        logger.info("Matrix user already exists: %s", username)
        return True

    logger.error(
        "Matrix register failed for %s: status=%s body=%s",
        username,
        response.status_code,
        body,
    )
    return False


def _login_matrix_user(
    client: httpx.Client,
    matrix_server: str,
    username: str,
    password: str,
) -> tuple[str, str]:
    payload = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": username},
        "password": password,
    }
    response = client.post(f"{matrix_server}/_matrix/client/v3/login", json=payload)
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token")
    device_id = data.get("device_id")
    if not token:
        raise RuntimeError(f"Matrix login returned no token for {username}")
    return token, device_id or ""


def _higress_post(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    ok_statuses: set[int] | None = None,
) -> bool:
    accepted = ok_statuses or {200, 201, 202, 409}
    try:
        response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Higress request failed %s: %s", url, exc)
        return False

    if response.status_code in accepted:
        return True

    logger.warning(
        "Higress request rejected: %s status=%s body=%s",
        url,
        response.status_code,
        response.text,
    )
    return False


def _load_known_models(workspace_dir: Path) -> list[dict[str, Any]]:
    candidates = [
        os.getenv("HICLAW_KNOWN_MODELS_FILE", ""),
        str(workspace_dir / "known-models.json"),
        "/opt/hiclaw/configs/known-models.json",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse known models from %s: %s", path, exc)
            continue

        if isinstance(loaded, list):
            return [m for m in loaded if isinstance(m, dict) and m.get("id")]
        if isinstance(loaded, dict) and isinstance(loaded.get("models"), list):
            return [m for m in loaded["models"] if isinstance(m, dict) and m.get("id")]

    return []


def _merge_models_in_openclaw(
    config_obj: dict[str, Any], known_models: list[dict[str, Any]]
) -> bool:
    providers = config_obj.setdefault("models", {}).setdefault("providers", {})
    hiclaw_provider = providers.setdefault("hiclaw-gateway", {})
    models = hiclaw_provider.setdefault("models", [])
    if not isinstance(models, list):
        return False

    existing_ids = {m.get("id") for m in models if isinstance(m, dict)}
    changed = False
    for model in known_models:
        model_id = model.get("id")
        if not model_id or model_id in existing_ids:
            continue
        models.append(model)
        existing_ids.add(model_id)
        changed = True

    if changed:
        aliases = {
            f"hiclaw-gateway/{m['id']}": {"alias": m["id"]}
            for m in models
            if isinstance(m, dict) and m.get("id")
        }
        defaults = config_obj.setdefault("agents", {}).setdefault("defaults", {})
        defaults["models"] = {**defaults.get("models", {}), **aliases}

    return changed


def wait_for_infrastructure(timeout: int = 120) -> bool:
    checks = [
        ("Higress Gateway", os.getenv("HICLAW_HIGRESS_HOST", "127.0.0.1"), 8080),
        ("Higress Console", os.getenv("HICLAW_HIGRESS_HOST", "127.0.0.1"), 8001),
        ("Tuwunel", os.getenv("HICLAW_TUWUNEL_HOST", "127.0.0.1"), 6167),
        ("MinIO", os.getenv("HICLAW_MINIO_HOST", "127.0.0.1"), 9000),
    ]

    logger.info("Waiting for infrastructure (timeout=%ss)", timeout)
    started = time.time()
    pending = {(name, host, port) for name, host, port in checks}

    while pending and (time.time() - started) < timeout:
        now_pending: set[tuple[str, str, int]] = set()
        for name, host, port in pending:
            try:
                with socket.create_connection((host, port), timeout=2):
                    logger.info("Infrastructure ready: %s (%s:%s)", name, host, port)
            except OSError:
                now_pending.add((name, host, port))
        pending = now_pending
        if pending:
            names = ", ".join(sorted(name for name, _, _ in pending))
            logger.info("Still waiting on: %s", names)
            time.sleep(2)

    if pending:
        names = ", ".join(sorted(name for name, _, _ in pending))
        logger.error("Infrastructure timeout; unavailable: %s", names)
        return False
    return True


def ensure_secrets(workspace_dir: str) -> dict[str, str]:
    workspace = Path(workspace_dir).expanduser()
    secrets_file = workspace / "hiclaw-secrets.env"
    persisted = _load_env_file(secrets_file)

    gateway_key = os.getenv("HICLAW_MANAGER_GATEWAY_KEY") or persisted.get(
        "HICLAW_MANAGER_GATEWAY_KEY"
    )
    manager_password = os.getenv("HICLAW_MANAGER_PASSWORD") or persisted.get(
        "HICLAW_MANAGER_PASSWORD"
    )

    if not gateway_key:
        gateway_key = py_secrets.token_hex(32)
        logger.info("Generated HICLAW_MANAGER_GATEWAY_KEY")
    if not manager_password:
        manager_password = py_secrets.token_hex(16)
        logger.info("Generated HICLAW_MANAGER_PASSWORD")

    resolved = {
        "HICLAW_MANAGER_GATEWAY_KEY": gateway_key,
        "HICLAW_MANAGER_PASSWORD": manager_password,
    }
    _write_env_file(secrets_file, resolved)
    return resolved


def register_matrix_accounts(
    secrets: dict[str, str], workspace_dir: str
) -> dict[str, str]:
    matrix_domain = os.getenv("HICLAW_MATRIX_DOMAIN", "matrix-local.hiclaw.io:18080")
    matrix_server = _matrix_server_url(matrix_domain)
    registration_token = os.getenv("HICLAW_REGISTRATION_TOKEN")
    admin_user = os.getenv("HICLAW_ADMIN_USER", "admin")
    admin_password = os.getenv("HICLAW_ADMIN_PASSWORD")
    manager_user = os.getenv("HICLAW_MANAGER_USER", "manager")
    manager_password = secrets["HICLAW_MANAGER_PASSWORD"]

    if not admin_password:
        raise RuntimeError("HICLAW_ADMIN_PASSWORD is required for Matrix registration")

    logger.info("Registering Matrix users on %s", matrix_server)

    with httpx.Client(timeout=20.0) as client:
        admin_registered = _register_matrix_user(
            client,
            matrix_server,
            admin_user,
            admin_password,
            registration_token,
        )
        manager_registered = _register_matrix_user(
            client,
            matrix_server,
            manager_user,
            manager_password,
            registration_token,
        )

        if not admin_registered or not manager_registered:
            raise RuntimeError("Matrix account registration failed")

        admin_token, _ = _login_matrix_user(
            client,
            matrix_server,
            admin_user,
            admin_password,
        )
        manager_token, manager_device_id = _login_matrix_user(
            client,
            matrix_server,
            manager_user,
            manager_password,
        )

    matrix_auth_path = Path(workspace_dir).expanduser() / ".hermes" / "matrix-auth"
    matrix_auth_path.parent.mkdir(parents=True, exist_ok=True)
    matrix_auth_path.write_text(
        json.dumps(
            {"access_token": manager_token, "device_id": manager_device_id},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    logger.info("Manager Matrix auth persisted to %s", matrix_auth_path)
    return {"admin_token": admin_token, "manager_token": manager_token}


def init_higress_console(secrets: dict[str, str]) -> bool:
    base_url = os.getenv(
        "HICLAW_HIGRESS_CONSOLE_URL", "http://hiclaw-local.hiclaw.io:8001"
    ).rstrip("/")
    admin_user = os.getenv("HICLAW_ADMIN_USER", "admin")
    admin_password = os.getenv("HICLAW_ADMIN_PASSWORD")
    if not admin_password:
        logger.error("HICLAW_ADMIN_PASSWORD is required for Higress init")
        return False

    logger.info("Initializing Higress Console at %s", base_url)

    with httpx.Client(timeout=20.0) as client:
        init_ok = _higress_post(
            client,
            f"{base_url}/apis/configs/v1/admingateway",
            {
                "name": admin_user,
                "password": admin_password,
                "displayName": admin_user,
            },
            ok_statuses={200, 201, 202, 400, 409},
        )
        if not init_ok:
            init_ok = _higress_post(
                client,
                f"{base_url}/apis/configs/v1/admingateway",
                {
                    "adminUser": {
                        "name": admin_user,
                        "password": admin_password,
                        "displayName": admin_user,
                    }
                },
                ok_statuses={200, 201, 202, 400, 409},
            )
        if not init_ok:
            return False

        login_response = client.post(
            f"{base_url}/api/v1/login",
            json={"username": admin_user, "password": admin_password},
        )
        if login_response.status_code >= 400:
            login_response = client.post(
                f"{base_url}/session/login",
                json={"username": admin_user, "password": admin_password},
            )
        if login_response.status_code >= 400:
            logger.error(
                "Higress login failed: status=%s body=%s",
                login_response.status_code,
                login_response.text,
            )
            return False

        login_data: dict[str, Any] = {}
        try:
            login_data = login_response.json()
        except Exception:
            pass

        auth_token = (
            login_data.get("token")
            or (login_data.get("data") or {}).get("token")
            or login_data.get("access_token")
        )
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None

        aigw_url = os.getenv(
            "HICLAW_AI_GATEWAY_URL", "http://aigw-local.hiclaw.io:8080"
        )
        route_ok = _higress_post(
            client,
            f"{base_url}/apis/configs/v1/routes",
            {
                "name": "hiclaw-v1",
                "uri": "/v1",
                "upstream": f"{aigw_url.rstrip('/')}/v1",
            },
            headers=headers,
        )
        if not route_ok:
            logger.warning("Higress route setup did not confirm success")

        consumer_ok = _higress_post(
            client,
            f"{base_url}/apis/configs/v1/consumers",
            {
                "name": "hiclaw-gateway",
                "key": secrets["HICLAW_MANAGER_GATEWAY_KEY"],
            },
            headers=headers,
        )
        if not consumer_ok:
            logger.warning("Higress consumer setup did not confirm success")

        skills_api = os.getenv("HICLAW_SKILLS_API_URL")
        if skills_api:
            mcp_ok = _higress_post(
                client,
                f"{base_url}/apis/configs/v1/mcpservers",
                {
                    "name": "skills-api",
                    "url": skills_api,
                },
                headers=headers,
            )
            if not mcp_ok:
                logger.warning("Higress MCP server setup did not confirm success")

    return True


def generate_manager_config(
    secrets: dict[str, str], manager_token: str, workspace_dir: str
) -> str:
    workspace = Path(workspace_dir).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = workspace / "hermes-config.yaml"

    config_text = """# Auto-generated by hermes-agent hiclaw Manager
version: "1"

hiclaw:
  manager:
    gateway_key: "${{HICLAW_MANAGER_GATEWAY_KEY}}"
    password: "${{HICLAW_MANAGER_PASSWORD}}"
    workspace: "${{HICLAW_WORKSPACE_DIR}}"

  infrastructure:
    matrix_domain: "${{HICLAW_MATRIX_DOMAIN}}"
    docker_proxy: "${{HICLAW_DOCKER_PROXY:-http://hiclaw-docker-proxy:2375}}"
    minio_bucket: "${{HICLAW_MINIO_BUCKET:-hiclaw}}"
    minio_prefix_tasks: "${{HICLAW_TASK_SPECS_PREFIX:-task-specs/}}"
    minio_prefix_results: "${{HICLAW_TASK_RESULTS_PREFIX:-task-results/}}"
    minio_access_key: "${{HICLAW_MINIO_USER}}"
    minio_secret_key: "${{HICLAW_MINIO_PASSWORD}}"

  llm:
    provider: "${{HICLAW_LLM_PROVIDER:-openai-compat}}"
    base_url: "${{HICLAW_OPENAI_BASE_URL}}"
    model: "${{HICLAW_DEFAULT_MODEL:-MiniMax-M2.7}}"
    api_key: "${{HICLAW_LLM_API_KEY}}"

platforms:
  matrix:
    homeserver: "http://${{HICLAW_MATRIX_DOMAIN}}"
    access_token: "${MANAGER_TOKEN}"
    require_mention: true
    encryption: false
"""

    config_path.write_text(config_text, encoding="utf-8")

    os.environ["HICLAW_MANAGER_GATEWAY_KEY"] = secrets["HICLAW_MANAGER_GATEWAY_KEY"]
    os.environ["HICLAW_MANAGER_PASSWORD"] = secrets["HICLAW_MANAGER_PASSWORD"]
    os.environ["MANAGER_TOKEN"] = manager_token

    logger.info("Manager config generated at %s", config_path)
    return str(config_path)


def _upgrade_with_mc(
    known_models: list[dict[str, Any]],
    bucket: str,
    workers_prefix: str,
) -> bool:
    endpoint = os.getenv("HICLAW_MC_HOST")
    minio_host = os.getenv("HICLAW_MINIO_HOST")
    minio_port = os.getenv("HICLAW_MINIO_PORT", "9000")
    if not endpoint and minio_host:
        endpoint = f"http://{minio_host}:{minio_port}"

    env = os.environ.copy()
    if endpoint:
        env["MC_HOST_hiclaw"] = endpoint

    with tempfile.TemporaryDirectory(prefix="hiclaw-workers-") as temp_dir:
        mirror_cmd = [
            "mc",
            "mirror",
            f"hiclaw/{bucket}/{workers_prefix}",
            temp_dir,
            "--overwrite",
        ]
        mirror_run = subprocess.run(
            mirror_cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if mirror_run.returncode != 0:
            logger.warning("mc mirror failed: %s", mirror_run.stderr.strip())
            return False

        base = Path(temp_dir)
        changed_files = 0
        for config_path in base.rglob("openclaw.json"):
            try:
                config_obj = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skipping invalid config %s: %s", config_path, exc)
                continue

            if not _merge_models_in_openclaw(config_obj, known_models):
                continue

            config_path.write_text(
                json.dumps(config_obj, indent=2) + "\n", encoding="utf-8"
            )
            relative = config_path.relative_to(base).as_posix()
            remote = f"hiclaw/{bucket}/{workers_prefix}/{relative}"
            copy_run = subprocess.run(
                ["mc", "cp", config_path.as_posix(), remote],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if copy_run.returncode != 0:
                logger.warning(
                    "Failed to upload upgraded config %s: %s",
                    remote,
                    copy_run.stderr.strip(),
                )
                continue
            changed_files += 1

        logger.info("Upgraded %s worker config(s) with mc", changed_files)
    return True


def _upgrade_with_minio_sdk(
    known_models: list[dict[str, Any]],
    bucket: str,
    workers_prefix: str,
) -> bool:
    try:
        from minio import Minio  # pyright: ignore[reportMissingImports]
    except ImportError:
        logger.error("MinIO SDK is not installed and mc is unavailable")
        return False

    endpoint = os.getenv("HICLAW_MINIO_ENDPOINT")
    if not endpoint:
        host = os.getenv("HICLAW_MINIO_HOST", "127.0.0.1")
        port = os.getenv("HICLAW_MINIO_PORT", "9000")
        endpoint = f"{host}:{port}"

    access_key = os.getenv("HICLAW_MINIO_USER")
    secret_key = os.getenv("HICLAW_MINIO_PASSWORD")
    secure = _env_bool("HICLAW_MINIO_SECURE", False)
    if not access_key or not secret_key:
        logger.error("Missing HICLAW_MINIO_USER/HICLAW_MINIO_PASSWORD for MinIO SDK")
        return False

    client = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )

    changed_files = 0
    for obj in client.list_objects(bucket, prefix=workers_prefix, recursive=True):
        if not obj.object_name.endswith("openclaw.json"):
            continue
        response = client.get_object(bucket, obj.object_name)
        try:
            raw = response.read()
        finally:
            response.close()
            response.release_conn()

        try:
            config_obj = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.warning("Skipping invalid config %s: %s", obj.object_name, exc)
            continue

        if not _merge_models_in_openclaw(config_obj, known_models):
            continue

        updated = json.dumps(config_obj, indent=2).encode("utf-8") + b"\n"
        client.put_object(
            bucket,
            obj.object_name,
            BytesIO(updated),
            length=len(updated),
            content_type="application/json",
        )
        changed_files += 1

    logger.info("Upgraded %s worker config(s) with MinIO SDK", changed_files)
    return True


def upgrade_worker_configs(workspace_dir: str) -> bool:
    workspace = Path(workspace_dir).expanduser()
    known_models = _load_known_models(workspace)
    if not known_models:
        logger.warning("No known-models.json found; skipping worker config upgrade")
        return False

    bucket = os.getenv("HICLAW_MINIO_BUCKET", "hiclaw")
    workers_prefix = os.getenv("HICLAW_WORKER_CONFIGS_PREFIX", "agents")

    if shutil.which("mc"):
        if _upgrade_with_mc(known_models, bucket, workers_prefix):
            return True
        logger.info("Falling back to MinIO SDK for worker config upgrade")

    return _upgrade_with_minio_sdk(known_models, bucket, workers_prefix)


def init_manager(workspace_dir: str | None = None) -> dict[str, Any]:
    workspace = workspace_dir
    if workspace is None:
        workspace = os.getenv("HICLAW_WORKSPACE_DIR", "/root/manager-workspace")
    if workspace is None:
        workspace = "/root/manager-workspace"
    logger.info("Starting hiclaw manager init sequence (workspace=%s)", workspace)

    infra_ready = wait_for_infrastructure()
    if not infra_ready:
        return {
            "success": False,
            "infrastructure_ready": False,
            "workspace_dir": workspace,
        }

    try:
        secrets = ensure_secrets(workspace)
        matrix = register_matrix_accounts(secrets, workspace)
        higress_ok = init_higress_console(secrets)
        config_path = generate_manager_config(
            secrets, matrix["manager_token"], workspace
        )
        worker_upgrade_ok = upgrade_worker_configs(workspace)
    except Exception as exc:
        logger.exception("Manager init failed: %s", exc)
        return {
            "success": False,
            "infrastructure_ready": True,
            "workspace_dir": workspace,
            "error": str(exc),
        }

    success = higress_ok and bool(matrix.get("manager_token"))
    return {
        "success": success,
        "infrastructure_ready": True,
        "workspace_dir": workspace,
        "secrets": secrets,
        "admin_token": matrix.get("admin_token", ""),
        "manager_token": matrix.get("manager_token", ""),
        "higress_initialized": higress_ok,
        "config_path": config_path,
        "worker_configs_upgraded": worker_upgrade_ok,
    }

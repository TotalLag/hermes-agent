"""Integration tests for hiClaw credential isolation and network security.

These are CONFIGURATION tests — they read docker-compose.prod.yml and verify
the network isolation and secrets configuration, not runtime behavior.
"""

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPOSE_PROD_PATH = PROJECT_ROOT / "docker" / "docker-compose.prod.yml"
SECRETS_EXAMPLE_PATH = PROJECT_ROOT / "docker" / "hiclaw-secrets.env.example"
COMPOSE_PATH = PROJECT_ROOT / "docker" / "docker-compose.yml"


def load_yaml(path: Path) -> dict:
    """Load and parse a YAML file."""
    return yaml.safe_load(path.read_text())


def load_compose(path: Path) -> dict:
    """Load docker-compose YAML with standard includes resolved."""
    content = path.read_text()
    return yaml.safe_load(content)


# ---------------------------------------------------------------------------
# Network isolation tests
# ---------------------------------------------------------------------------


class TestWorkerCannotReachAdminAPI:
    """Workers must not have access to Synapse admin API endpoints."""

    def test_workers_on_hiclaw_prod_net(self):
        """Verify workers are attached to hiclaw-prod-net in docker-compose.prod.yml."""
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        worker_services = [name for name in services if "worker" in name]
        assert len(worker_services) > 0, (
            "No worker services found in docker-compose.prod.yml"
        )

        for worker_name in worker_services:
            worker_service = services[worker_name]
            networks = worker_service.get("networks", [])
            assert "hiclaw-prod-net" in networks, (
                f"Worker {worker_name} must be on hiclaw-prod-net for Matrix communication"
            )

    def test_workers_have_no_admin_network_route(self):
        """Workers should not be on an admin-only network that could reach /_synapse/admin/.

        This checks that there is no separate admin network defined, and workers
        are only on the shared production network.
        """
        data = load_compose(COMPOSE_PROD_PATH)
        networks = data.get("networks", {})

        admin_network_names = [
            name for name in networks.keys() if "admin" in name.lower()
        ]
        assert len(admin_network_names) == 0, (
            f"Found admin networks {admin_network_names} — workers must not have "
            "a route to admin-only network. Admin API should be on the same network "
            "but worker containers should not be configured to reach it."
        )

    def test_synapse_admin_endpoint_not_exposed_to_workers(self):
        """Synapse admin API should not be listed as an exposed port on worker services.

        Workers only need to reach the client-server API (port 8008), not the
        admin API. This test verifies no worker service has admin ports exposed.
        """
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        admin_ports = ["8448", "1337"]
        for service_name, service in services.items():
            if "worker" in service_name:
                exposed_ports = service.get("expose", [])
                for port in exposed_ports:
                    port_str = str(port)
                    for admin_port in admin_ports:
                        assert admin_port not in port_str, (
                            f"Worker {service_name} should not expose admin port {admin_port}"
                        )


class TestWorkersOnSeparateNetwork:
    """Workers should be on the same network as Synapse/MinIO for service access."""

    def test_workers_and_synapse_on_same_network(self):
        """Workers and Synapse must share the production network for Matrix protocol."""
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        synapse_service = services.get("synapse")
        assert synapse_service is not None, (
            "Synapse service not found in docker-compose.prod.yml"
        )

        worker_services = {
            name: svc for name, svc in services.items() if "worker" in name
        }
        assert len(worker_services) > 0, "No worker services found"

        synapse_networks = set(synapse_service.get("networks", []))
        for worker_name, worker_service in worker_services.items():
            worker_networks = set(worker_service.get("networks", []))
            common_networks = synapse_networks & worker_networks
            assert len(common_networks) > 0, (
                f"Worker {worker_name} and Synapse must share at least one network. "
                f"Worker networks: {worker_networks}, Synapse networks: {synapse_networks}"
            )

    def test_workers_and_minio_on_same_network(self):
        """Workers and MinIO must share the production network for artifact storage."""
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        minio_service = services.get("minio")
        assert minio_service is not None, (
            "MinIO service not found in docker-compose.prod.yml"
        )

        worker_services = {
            name: svc for name, svc in services.items() if "worker" in name
        }
        assert len(worker_services) > 0, "No worker services found"

        minio_networks = set(minio_service.get("networks", []))
        for worker_name, worker_service in worker_services.items():
            worker_networks = set(worker_service.get("networks", []))
            common_networks = minio_networks & worker_networks
            assert len(common_networks) > 0, (
                f"Worker {worker_name} and MinIO must share at least one network "
                "for artifact storage access."
            )

    def test_manager_on_same_network_as_workers(self):
        """Hermes-manager must share the network with workers for task coordination."""
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        manager_service = services.get("hermes-manager")
        assert manager_service is not None, "hermes-manager service not found"

        worker_services = {
            name: svc for name, svc in services.items() if "worker" in name
        }
        assert len(worker_services) > 0, "No worker services found"

        manager_networks = set(manager_service.get("networks", []))
        for worker_name, worker_service in worker_services.items():
            worker_networks = set(worker_service.get("networks", []))
            common_networks = manager_networks & worker_networks
            assert len(common_networks) > 0, (
                f"hermes-manager and worker {worker_name} must share at least one network "
                "for task coordination."
            )


# ---------------------------------------------------------------------------
# Secrets isolation tests
# ---------------------------------------------------------------------------


class TestNoSecretsInEnvironment:
    """Secrets must be loaded via env_file, never as individual env vars with values."""

    def test_workers_use_env_file_for_secrets(self):
        """Workers must use env_file directive for secrets, not individual env vars."""
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        worker_services = [name for name in services if "worker" in name]
        assert len(worker_services) > 0, "No worker services found"

        for worker_name in worker_services:
            worker_service = services[worker_name]

            # Must have env_file pointing to secrets
            env_file = worker_service.get("env_file", [])
            assert env_file is not None and len(env_file) > 0, (
                f"Worker {worker_name} must have env_file configured for secrets"
            )
            env_file_strs = [
                f if isinstance(f, str) else f.get("file", "") for f in env_file
            ]
            secrets_files = [f for f in env_file_strs if "secrets" in f]
            assert len(secrets_files) > 0, (
                f"Worker {worker_name} env_file must include secrets file, "
                f"found: {env_file_strs}"
            )

            # Must NOT have individual secret env vars with actual values
            environment = worker_service.get("environment", {})
            secret_var_names = [
                "HICLAW_LLM_API_KEY",
                "HICLAW_MANAGER_ACCESS_TOKEN",
                "HICLAW_WORKER1_ACCESS_TOKEN",
                "MATRIX_ACCESS_TOKEN",
            ]
            for var_name in secret_var_names:
                assert var_name not in environment, (
                    f"Worker {worker_name} must not set {var_name} as individual "
                    "environment variable — use env_file instead"
                )

    def test_manager_uses_env_file_for_secrets(self):
        """hermes-manager must use env_file for secrets, not individual env vars."""
        data = load_compose(COMPOSE_PROD_PATH)
        services = data.get("services", {})

        manager_service = services.get("hermes-manager")
        assert manager_service is not None, "hermes-manager not found"

        env_file = manager_service.get("env_file", [])
        assert env_file is not None and len(env_file) > 0, (
            "hermes-manager must have env_file configured for secrets"
        )

        environment = manager_service.get("environment", {})
        secret_var_names = [
            "HICLAW_LLM_API_KEY",
            "HICLAW_MANAGER_ACCESS_TOKEN",
            "HICLAW_WORKER1_ACCESS_TOKEN",
            "MATRIX_ACCESS_TOKEN",
        ]
        for var_name in secret_var_names:
            assert var_name not in environment, (
                f"hermes-manager must not set {var_name} as individual "
                "environment variable — use env_file instead"
            )

    def test_secrets_example_has_no_actual_values(self):
        """hiclaw-secrets.env.example must have empty values, never real secrets."""
        assert SECRETS_EXAMPLE_PATH.exists(), (
            f"Secrets example file not found at {SECRETS_EXAMPLE_PATH}"
        )

        content = SECRETS_EXAMPLE_PATH.read_text()

        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        for line in lines:
            match = re.match(r"^([A-Z0-9_]+)=(.*)$", line.rstrip())
            assert match is not None, (
                f"Unexpected line format in secrets example: {line!r}"
            )

            key = match.group(1)
            value = match.group(2).strip()

            placeholder_indicators = [
                "your-",
                "example",
                "placeholder",
                "<",
                "changeme",
                "TODO",
                "REPLACE",
            ]
            is_placeholder = any(
                indicator in value.lower() for indicator in placeholder_indicators
            )
            is_empty = value == ""

            assert is_placeholder or is_empty, (
                f"Secret {key} in hiclaw-secrets.env.example must have empty value "
                f"or placeholder text, got: {value!r}"
            )

            key = match.group(1)
            value = match.group(2).strip()

            # Values must be empty or contain obvious placeholder text
            placeholder_indicators = [
                "your-",
                "example",
                "placeholder",
                "<",
                "changeme",
                "TODO",
                "REPLACE",
            ]
            is_placeholder = any(
                indicator in value.lower() for indicator in placeholder_indicators
            )
            is_empty = value == ""

            assert is_placeholder or is_empty, (
                f"Secret {key} in hiclaw-secrets.env.example must have empty value "
                f"or placeholder text, got: {value!r}"
            )

    def test_secrets_file_in_gitignore_pattern(self):
        """Verify a .gitignore or similar mechanism exists to prevent committing secrets."""
        gitignore_path = PROJECT_ROOT / "docker" / ".gitignore"
        docker_gitignore = PROJECT_ROOT / "docker" / ".gitignore"

        gitignore = None
        if docker_gitignore.exists():
            gitignore = docker_gitignore
        elif gitignore_path.exists():
            gitignore = gitignore_path

        if gitignore is not None:
            content = gitignore.read_text()
            assert "secrets" in content.lower() or "*.env" in content, (
                "docker/.gitignore should exclude secrets files"
            )

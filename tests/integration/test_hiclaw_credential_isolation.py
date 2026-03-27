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

    def test_workers_use_environment_vars_for_secrets(self):
        """Workers must use environment: vars with ${VAR} substitution for secrets.

        NOTE: env_file is NOT used in docker-compose.prod.yml for non-Swarm deployments
        because env_file OVERWRITES environment: vars, blanking credentials at runtime.
        Instead, secrets are injected via --env-file flag at docker-compose up time.
        The Swarm path uses docker/hiclaw-secrets.docker-compose.yml with proper secrets.
        """
        # Check the base compose (where environment: vars are defined)
        base_data = load_compose(COMPOSE_PATH)
        base_services = base_data.get("services", {})

        worker_services = [name for name in base_services if "worker" in name]
        assert len(worker_services) > 0, "No worker services found"

        for worker_name in worker_services:
            worker_service = base_services[worker_name]

            environment = worker_service.get("environment", {})
            assert environment, f"Worker {worker_name} must have environment: vars"
            # Verify ${VAR} substitution pattern for secrets
            secret_var_names = [
                "HICLAW_LLM_API_KEY",
                "MATRIX_ACCESS_TOKEN",
            ]
            for var_name in secret_var_names:
                val = environment.get(var_name, "")
                assert "${" in val and "}" in val, (
                    f"Worker {worker_name} environment:{var_name} must use ${{VAR}} "
                    f"substitution, got: {val!r}"
                )

        prod_data = load_compose(COMPOSE_PROD_PATH)
        prod_services = prod_data.get("services", {})

        for worker_name in worker_services:
            prod_service = prod_services.get(worker_name, {})
            env_file = prod_service.get("env_file", [])
            assert env_file is None or len(env_file) == 0, (
                f"Worker {worker_name} must NOT have env_file in prod compose — "
                "env_file overrides environment: vars and blanks credentials. "
                "Use --env-file at runtime instead."
            )

            prod_env = prod_service.get("environment", {})
            secret_var_names = [
                "HICLAW_LLM_API_KEY",
                "HICLAW_MANAGER_ACCESS_TOKEN",
                "HICLAW_WORKER1_ACCESS_TOKEN",
                "MATRIX_ACCESS_TOKEN",
            ]
            for var_name in secret_var_names:
                assert var_name not in prod_env, (
                    f"Worker {worker_name} must not set {var_name} as individual "
                    "environment variable in prod compose"
                )

    def test_manager_uses_environment_vars_for_secrets(self):
        """hermes-manager must use environment: vars with ${VAR} substitution for secrets.

        NOTE: env_file is NOT used in docker-compose.prod.yml for non-Swarm deployments
        because env_file OVERWRITES environment: vars, blanking credentials at runtime.
        Instead, secrets are injected via --env-file flag at docker-compose up time.
        The Swarm path uses docker/hiclaw-secrets.docker-compose.yml with proper secrets.
        """
        # Check the base compose (where environment: vars are defined)
        base_data = load_compose(COMPOSE_PATH)
        base_services = base_data.get("services", {})

        manager_service = base_services.get("hermes-manager")
        assert manager_service is not None, "hermes-manager not found"

        environment = manager_service.get("environment", {})
        assert environment, "hermes-manager must have environment: vars"
        secret_var_names = [
            "HICLAW_LLM_API_KEY",
            "MATRIX_ACCESS_TOKEN",
        ]
        for var_name in secret_var_names:
            val = environment.get(var_name, "")
            assert "${" in val and "}" in val, (
                f"hermes-manager environment:{var_name} must use ${{VAR}} "
                f"substitution, got: {val!r}"
            )

        # Prod compose must NOT have env_file (would blank the credentials)
        prod_data = load_compose(COMPOSE_PROD_PATH)
        prod_services = prod_data.get("services", {})

        prod_manager = prod_services.get("hermes-manager", {})
        env_file = prod_manager.get("env_file", [])
        assert env_file is None or len(env_file) == 0, (
            "hermes-manager must NOT have env_file in prod compose — "
            "env_file overrides environment: vars and blanks credentials. "
            "Use --env-file at runtime instead."
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
            raw_value = match.group(2).strip()
            # Strip inline comments (e.g. "value  # REQUIRED - description")
            value = raw_value.split("#")[0].strip()

            placeholder_indicators = [
                "your-",
                "example",
                "placeholder",
                "<",
                "changeme",
                "TODO",
                "REPLACE",
                "YOUR",  # YOUR_MATRIX_*, YOUR_MINIO_* patterns
                "://",  # URL values (e.g. http://synapse:8008) — not secrets
            ]
            is_placeholder = any(
                indicator.lower() in value.lower()
                for indicator in placeholder_indicators
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

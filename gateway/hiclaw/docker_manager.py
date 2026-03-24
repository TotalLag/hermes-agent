"""
Docker container manager for HiClaw workers using the docker-py SDK.
No shell commands — all operations go through the Docker API.
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import docker
from docker.errors import APIError, NotFound

logger = logging.getLogger(__name__)

WORKER_IMAGE = os.getenv("HICLAW_WORKER_IMAGE", "hermes-worker:latest")
MANAGER_CONTAINER_NAME = "hermes-manager"
WORKER_CONTAINER_PREFIX = "hermes-worker-"
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")


class ContainerStatus(Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"
    NOT_FOUND = "not_found"


@dataclass
class WorkerContainer:
    name: str
    status: ContainerStatus
    health: Optional[str] = None
    created_at: Optional[str] = None
    image: Optional[str] = None


class DockerManager:
    """
    Manages hermes-worker-* containers via the Docker API.
    The Manager process runs as the hermes-manager container and shares
    the host's Docker socket to control worker containers.
    """

    def __init__(
        self,
        docker_host: str = DOCKER_HOST,
        worker_image: str = WORKER_IMAGE,
    ):
        self._client = docker.DockerClient(base_url=docker_host)
        self._worker_image = worker_image

    def launch_worker(
        self,
        worker_name: str,
        manager_room_id: str,
        manager_mxid: str,
        worker_version: str = "1.0.0",
        extra_env: Optional[Dict[str, str]] = None,
        remove_if_exists: bool = False,
    ) -> WorkerContainer:
        """
        Launch a new hermes-worker-{worker_name} container.

        Args:
            worker_name:        Unique name suffix
            manager_room_id:    Matrix room ID the worker should join
            manager_mxid:      Manager's Matrix user ID
            worker_version:     Version string
            extra_env:         Additional env vars for the container
            remove_if_exists:   Remove existing container with the same name first

        Returns:
            WorkerContainer with the launched container's status
        Raises:
            docker.errors.APIError: If the container fails to launch
        """
        container_name = f"{WORKER_CONTAINER_PREFIX}{worker_name}"

        if remove_if_exists:
            try:
                self._client.containers.get(container_name).remove(force=True)
            except NotFound:
                pass
            except APIError as e:
                logger.warning(
                    "DockerManager: failed to remove %s: %s", container_name, e
                )

        env: Dict[str, str] = dict(os.environ)
        env["HERMES_WORKER_ID"] = f"{WORKER_CONTAINER_PREFIX}{worker_name}"
        env["HERMES_WORKER_NAME"] = worker_name
        env["HERMES_WORKER_VERSION"] = worker_version
        env["HICLAW_WORKER_NAME"] = worker_name
        env["HICLAW_WORKER_VERSION"] = worker_version
        env["HICLAW_MANAGER_ROOM_ID"] = manager_room_id
        env["HICLAW_MANAGER_MXID"] = manager_mxid
        env["DOCKER_HOST"] = DOCKER_HOST
        if extra_env:
            env.update(extra_env)
        env = {k: str(v) for k, v in env.items()}

        try:
            container = self._client.containers.run(
                image=self._worker_image,
                name=container_name,
                detach=True,
                environment=env,
                volumes={DOCKER_HOST: {"bind": DOCKER_HOST, "mode": "ro"}},
                restart_policy={"Name": "unless-stopped"},
                force_pull=False,
                remove=False,
            )
            logger.info(
                "DockerManager: launched %s (id=%s)",
                container_name,
                container.id[:12],
            )
            return self._container_to_worker(container)
        except APIError as e:
            logger.error("DockerManager: failed to launch %s: %s", container_name, e)
            raise

    def stop_worker(self, worker_name: str, timeout: int = 30) -> bool:
        """Gracefully stop a hermes-worker-{worker_name} container."""
        container_name = f"{WORKER_CONTAINER_PREFIX}{worker_name}"
        try:
            self._client.containers.get(container_name).stop(timeout=timeout)
            logger.info("DockerManager: stopped %s", container_name)
            return True
        except NotFound:
            return False
        except APIError as e:
            logger.error("DockerManager: failed to stop %s: %s", container_name, e)
            return False

    def remove_worker(self, worker_name: str, force: bool = False) -> bool:
        """Remove a hermes-worker-{worker_name} container."""
        container_name = f"{WORKER_CONTAINER_PREFIX}{worker_name}"
        try:
            self._client.containers.get(container_name).remove(force=force)
            logger.info("DockerManager: removed %s", container_name)
            return True
        except NotFound:
            return False
        except APIError as e:
            logger.error("DockerManager: failed to remove %s: %s", container_name, e)
            return False

    def get_worker(self, worker_name: str) -> WorkerContainer:
        """Get the status of a specific worker container."""
        container_name = f"{WORKER_CONTAINER_PREFIX}{worker_name}"
        try:
            return self._container_to_worker(
                self._client.containers.get(container_name)
            )
        except NotFound:
            return WorkerContainer(
                name=container_name, status=ContainerStatus.NOT_FOUND
            )
        except APIError as e:
            logger.error("DockerManager: failed to get %s: %s", container_name, e)
            return WorkerContainer(name=container_name, status=ContainerStatus.UNKNOWN)

    def list_workers(self, all_states: bool = False) -> List[WorkerContainer]:
        """List all hermes-worker-* containers."""
        try:
            return [
                self._container_to_worker(c)
                for c in self._client.containers.list(
                    all=all_states,
                    filters={"name": [WORKER_CONTAINER_PREFIX]},
                )
            ]
        except APIError as e:
            logger.error("DockerManager: list failed: %s", e)
            return []

    def get_worker_logs(
        self, worker_name: str, tail: int = 100, timestamps: bool = False
    ) -> str:
        """Get stdout/stderr logs of a hermes-worker-{worker_name} container."""
        container_name = f"{WORKER_CONTAINER_PREFIX}{worker_name}"
        try:
            container = self._client.containers.get(container_name)
            return container.logs(
                tail=tail, timestamps=timestamps, stdout=True, stderr=True
            ).decode("utf-8", errors="replace")
        except NotFound:
            return f"Container {container_name} not found"
        except APIError as e:
            return f"Error fetching logs: {e}"

    def restart_worker(self, worker_name: str, timeout: int = 30) -> bool:
        """Restart a hermes-worker-{worker_name} container."""
        container_name = f"{WORKER_CONTAINER_PREFIX}{worker_name}"
        try:
            self._client.containers.get(container_name).restart(timeout=timeout)
            logger.info("DockerManager: restarted %s", container_name)
            return True
        except NotFound:
            return False
        except APIError as e:
            logger.error("DockerManager: failed to restart %s: %s", container_name, e)
            return False

    def get_manager(self) -> Optional[WorkerContainer]:
        """Get the hermes-manager container status."""
        try:
            return self._container_to_worker(
                self._client.containers.get(MANAGER_CONTAINER_NAME)
            )
        except NotFound:
            return None
        except APIError as e:
            logger.error("DockerManager: failed to get manager: %s", e)
            return None

    def is_docker_available(self) -> bool:
        """Check if the Docker socket is accessible."""
        try:
            self._client.ping()
            return True
        except Exception as e:
            logger.warning("DockerManager: Docker socket unavailable: %s", e)
            return False

    def _container_to_worker(self, container) -> WorkerContainer:
        status_map = {
            "running": ContainerStatus.RUNNING,
            "exited": ContainerStatus.STOPPED,
            "paused": ContainerStatus.STOPPED,
            "restarting": ContainerStatus.RUNNING,
            "dead": ContainerStatus.STOPPED,
        }
        status = status_map.get(container.status, ContainerStatus.UNKNOWN)
        health = None
        try:
            state = container.attrs.get("State", {})
            health = state.get("Health", {}).get("Status")
        except Exception:
            pass
        return WorkerContainer(
            name=container.name,
            status=status,
            health=health,
            created_at=container.attrs.get("Created"),
            image=container.attrs.get("Config", {}).get("Image"),
        )

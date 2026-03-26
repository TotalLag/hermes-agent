"""Integration tests for hiClaw worker scaling via DockerManager."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))

from gateway.hiclaw.docker_manager import (
    DockerManager,
    WorkerContainer,
    ContainerStatus,
)


@pytest.fixture
def mock_docker_client():
    return MagicMock()


@pytest.fixture
def docker_manager(mock_docker_client):
    with patch(
        "gateway.hiclaw.docker_manager.docker.DockerClient",
        return_value=mock_docker_client,
    ):
        yield DockerManager()


def make_mock_container(name, status="running", worker_id="abc123"):
    mock = MagicMock()
    mock.name = name
    mock.status = status
    mock.id = worker_id
    mock.attrs = {
        "Config": {"Image": "hermes-worker:latest"},
        "State": {"Status": status},
    }
    return mock


class TestScalingUp:
    def test_launch_worker_correct_env_vars(self, docker_manager, mock_docker_client):
        mock_container = make_mock_container("hermes-worker-test-worker")
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_manager.launch_worker(
            worker_name="test-worker",
            manager_room_id="!room:hermes.local",
            manager_mxid="@manager:hermes.local",
        )

        assert isinstance(result, WorkerContainer)
        assert result.status == ContainerStatus.RUNNING
        mock_docker_client.containers.run.assert_called_once()

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert "HICLAW_WORKER_NAME" in call_kwargs["environment"]
        assert "HICLAW_MANAGER_ROOM_ID" in call_kwargs["environment"]
        assert "HICLAW_MANAGER_MXID" in call_kwargs["environment"]
        assert call_kwargs["environment"]["HICLAW_WORKER_NAME"] == "test-worker"
        assert (
            call_kwargs["environment"]["HICLAW_MANAGER_ROOM_ID"] == "!room:hermes.local"
        )
        assert (
            call_kwargs["environment"]["HICLAW_MANAGER_MXID"] == "@manager:hermes.local"
        )

    def test_launch_multiple_workers(self, docker_manager, mock_docker_client):
        for i in range(3):
            mock_container = make_mock_container(f"hermes-worker-worker-{i}")
            mock_docker_client.containers.run.return_value = mock_container

            result = docker_manager.launch_worker(
                worker_name=f"worker-{i}",
                manager_room_id="!room:hermes.local",
                manager_mxid="@manager:hermes.local",
            )

            assert isinstance(result, WorkerContainer)
            assert result.status == ContainerStatus.RUNNING

        assert mock_docker_client.containers.run.call_count == 3

    def test_launch_worker_with_extra_env(self, docker_manager, mock_docker_client):
        mock_container = make_mock_container("hermes-worker-special")
        mock_docker_client.containers.run.return_value = mock_container

        docker_manager.launch_worker(
            worker_name="special",
            manager_room_id="!room:h.local",
            manager_mxid="@m:h.local",
            extra_env={"CUSTOM_KEY": "custom_val"},
        )

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert "CUSTOM_KEY" in call_kwargs["environment"]
        assert call_kwargs["environment"]["CUSTOM_KEY"] == "custom_val"


class TestScalingDown:
    def test_stop_worker(self, docker_manager, mock_docker_client):
        mock_container = make_mock_container("hermes-worker-worker-1")
        mock_docker_client.containers.get.return_value = mock_container

        result = docker_manager.stop_worker("worker-1")

        assert result is True
        mock_container.stop.assert_called_once()

    def test_stop_worker_not_found(self, docker_manager, mock_docker_client):
        from docker.errors import NotFound

        mock_docker_client.containers.get.side_effect = NotFound("not found")

        result = docker_manager.stop_worker("nonexistent")

        assert result is False

    def test_remove_worker(self, docker_manager, mock_docker_client):
        mock_container = make_mock_container("hermes-worker-worker-1")
        mock_docker_client.containers.get.return_value = mock_container

        result = docker_manager.remove_worker("worker-1")

        assert result is True
        mock_container.remove.assert_called_once()

    def test_remove_worker_after_stop(self, docker_manager, mock_docker_client):
        mock_container = make_mock_container("hermes-worker-worker-1")
        mock_docker_client.containers.get.return_value = mock_container

        stop_result = docker_manager.stop_worker("worker-1")
        assert stop_result is True

        remove_result = docker_manager.remove_worker("worker-1")
        assert remove_result is True


class TestScalingListWorkers:
    def test_list_workers_returns_worker_containers(
        self, docker_manager, mock_docker_client
    ):
        mock_containers = []
        for i in range(3):
            c = make_mock_container(f"hermes-worker-{i}", status="running")
            mock_containers.append(c)

        mock_docker_client.containers.list.return_value = mock_containers

        result = docker_manager.list_workers()

        assert len(result) == 3
        for worker in result:
            assert isinstance(worker, WorkerContainer)
            assert worker.status == ContainerStatus.RUNNING

    def test_list_workers_filter_running(self, docker_manager, mock_docker_client):
        mock_containers = [
            make_mock_container("hermes-worker-1", status="running"),
            make_mock_container("hermes-worker-2", status="exited"),
        ]

        mock_docker_client.containers.list.return_value = mock_containers

        result = docker_manager.list_workers(all_states=True)

        assert len(result) == 2
        for worker in result:
            assert isinstance(worker, WorkerContainer)

    def test_list_workers_empty(self, docker_manager, mock_docker_client):
        mock_docker_client.containers.list.return_value = []

        result = docker_manager.list_workers()

        assert len(result) == 0

    def test_get_worker(self, docker_manager, mock_docker_client):
        mock_container = make_mock_container("hermes-worker-1", status="running")
        mock_docker_client.containers.get.return_value = mock_container

        result = docker_manager.get_worker("1")

        assert isinstance(result, WorkerContainer)
        assert result.status == ContainerStatus.RUNNING
        assert result.name == "hermes-worker-1"

    def test_get_worker_not_found(self, docker_manager, mock_docker_client):
        from docker.errors import NotFound

        mock_docker_client.containers.get.side_effect = NotFound("not found")

        result = docker_manager.get_worker("nonexistent")

        assert isinstance(result, WorkerContainer)
        assert result.status == ContainerStatus.NOT_FOUND

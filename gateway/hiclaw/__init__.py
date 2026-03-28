"""hiClaw — Hermes-powered worker cluster management.

Modules:
    protocol — Natural language Matrix message parsing and encoding.
    manager_handler — Routes hiClaw worker messages via MCP tools.
"""

from gateway.hiclaw.manager_handler import HiClawManagerHandler
from gateway.hiclaw.protocol import TaskAssignEncoder, WorkerMessageParser

__all__ = ["WorkerMessageParser", "TaskAssignEncoder", "HiClawManagerHandler"]

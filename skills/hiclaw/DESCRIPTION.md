---
description: Skills for HiClaw Hermes agent-as-worker management via Matrix DM command protocol. Covers worker lifecycle (register, heartbeat, deregister), task dispatch, natural language task protocol, Matrix DM integration, and worker self-registration on first boot. Container naming uses hermes-manager / hermes-worker-{name} to avoid collision with hiclaw-manager / hiclaw-worker-{name}.
---

# HiClaw — Hermes Agent-as-Worker Management

Skills for managing Hermes agents as distributed workers via Matrix direct messages. The Manager (hermes-manager) assigns tasks to Workers (hermes-worker-{name}) through a natural language Matrix DM protocol using `//task-assign` and `//task-result` markers.

## Architecture

| Component | Container Name | Role |
|-----------|---------------|------|
| Manager | `hermes-manager` | Assigns tasks to workers, monitors heartbeats, tracks task state |
| Worker | `hermes-worker-{name}` | Self-registers on boot, receives and executes tasks, reports results |

## Data Models

- `WorkerRegistry` (`skills/hiclaw/worker_registry.py`) — tracks registered workers, status, capabilities, heartbeat timestamps
- `ManagerState` (`skills/hiclaw/manager_state.py`) — tracks Manager mode, tasks, and statistics
- `WorkerMessageParser` (`skills/hiclaw/manager_handler.py`) — parses JSON messages from workers in the Manager Matrix room

## Skill Topics

| Skill | Purpose |
|-------|---------|
| `self-registration` | Worker self-registration on first boot |
| `worker-lifecycle` | Worker lifecycle: register, heartbeat, deregister |
| `task-protocol` | Natural language task protocol with `//task-assign` and `//task-result` markers |
| `task-dispatch` | Task dispatch from Manager to workers |
| `matrix-integration` | Matrix DM handling for Manager-Worker comms |
| `stale-cleanup` | Periodic cleanup of stale workers and tasks (cron scheduled) |

## Timing

- **Worker heartbeat interval:** 2 minutes (much more responsive than HiClaw's hourly)
- **Manager heartbeat check interval:** 5 minutes

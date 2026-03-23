#!/usr/bin/env python3
"""
hiclaw Config Transform

Transforms hiclaw's openclaw.json format to Hermes Agent's config.yaml format.
Designed to run at container startup before Hermes launches.

Usage:
    python hiclaw_config_transform.py <openclaw.json> <output.yaml>

Environment Variables (optional override):
    HICLAW_MODEL        - Override model selection
    HICLAW_PROVIDER     - Override provider
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_openclaw_json(path: str) -> Dict[str, Any]:
    """Load and parse hiclaw openclaw.json configuration."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Input file not found: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
        sys.exit(1)


def normalize_named_mapping(
    data: Dict[str, Any], singular_key: str
) -> List[Dict[str, Any]]:
    """Normalize a mapping-based section into Hermes-friendly list format."""
    if not data:
        return []
    result = []
    for key, value in data.items():
        if isinstance(value, dict):
            item = {"name": key}
            item.update(value)
            result.append(item)
        else:
            result.append({"name": key, singular_key: value})
    return result


def transform_model(model: str, provider: Optional[str]) -> Dict[str, Any]:
    """Transform model specification to Hermes models section."""
    if not model:
        return {}

    # Handle provider:model format
    if "/" in model:
        parts = model.split("/", 1)
        effective_provider = parts[0]
        effective_model = parts[1]
    else:
        effective_provider = provider or "openrouter"
        effective_model = model

    return {
        "default": {
            "id": effective_model,
            "provider": effective_provider,
        }
    }


def transform_channels(channels: Any) -> List[Dict[str, Any]]:
    """Transform hiclaw channels to Hermes channels format."""
    if not channels:
        return []

    result = []
    if isinstance(channels, dict):
        for name, config in channels.items():
            channel = {"name": name}
            if isinstance(config, dict):
                channel.update(config)
            elif isinstance(config, str):
                channel["type"] = config
            result.append(channel)
    elif isinstance(channels, list):
        for item in channels:
            if isinstance(item, str):
                result.append({"name": item, "type": item})
            elif isinstance(item, dict):
                result.append(item)
    return result


def transform_toolsets(toolsets: Any) -> Dict[str, List[str]]:
    """Transform hiclaw toolsets to Hermes tools structure."""
    if not toolsets:
        return {"default": []}

    if isinstance(toolsets, list):
        return {"default": toolsets}
    elif isinstance(toolsets, dict):
        return toolsets
    return {"default": []}


def transform_mcp_servers(mcp_servers: Any) -> Dict[str, Any]:
    """Transform hiclaw mcpServers to Hermes mcpServers format."""
    if not mcp_servers:
        return {}

    if isinstance(mcp_servers, dict):
        return mcp_servers
    return {}


def transform_ai_gateway(ai_gateway: Any) -> Dict[str, Any]:
    """Transform hiclaw aiGateway to Hermes aiGateway format."""
    if not ai_gateway:
        return {}

    if isinstance(ai_gateway, dict):
        return ai_gateway
    return {}


def transform_skills(skills: Any) -> List[Dict[str, Any]]:
    """Transform hiclaw skills to Hermes skills format."""
    if not skills:
        return []

    if isinstance(skills, list):
        return [{"name": s} if isinstance(s, str) else s for s in skills]
    elif isinstance(skills, dict):
        return normalize_named_mapping(skills, "enabled")
    return []


def transform_memory(memory: Any) -> Dict[str, Any]:
    """Transform hiclaw memory to Hermes memory format."""
    if not memory:
        return {"enabled": False}

    if isinstance(memory, dict):
        result = {"enabled": True}
        result.update(memory)
        return result
    return {"enabled": True}


def transform_config(openclaw: Dict[str, Any]) -> Dict[str, Any]:
    """Transform complete hiclaw openclaw.json to Hermes config.yaml."""
    output = {
        "version": "1.0",
        "providers": {},
        "models": {},
        "channels": [],
        "tools": {},
        "skills": [],
        "memory": {"enabled": False},
        "mcpServers": {},
    }

    # Track unknown fields
    known_fields = {
        "model",
        "channels",
        "toolsets",
        "skills",
        "memory",
        "mcpServers",
        "aiGateway",
        "provider",
    }
    unknown_fields = set(openclaw.keys()) - known_fields
    for field in unknown_fields:
        logger.warning(f"Unknown top-level field '{field}' - skipping")

    # Transform each section
    # Model
    model = openclaw.get("model")
    provider = openclaw.get("provider")
    if model:
        output["models"] = transform_model(model, provider)
        logger.info(f"Transformed model: {model}")

    # Channels
    channels = openclaw.get("channels")
    output["channels"] = transform_channels(channels)
    if channels:
        logger.info(f"Transformed {len(output['channels'])} channel(s)")

    # Toolsets
    toolsets = openclaw.get("toolsets")
    output["tools"] = transform_toolsets(toolsets)
    if toolsets:
        logger.info(f"Transformed toolsets")

    # MCP Servers
    mcp_servers = openclaw.get("mcpServers")
    output["mcpServers"] = transform_mcp_servers(mcp_servers)
    if mcp_servers:
        logger.info(f"Transformed mcpServers")

    # AI Gateway
    ai_gateway = openclaw.get("aiGateway")
    if ai_gateway:
        output["providers"]["aiGateway"] = transform_ai_gateway(ai_gateway)
        logger.info("Transformed aiGateway")

    # Skills
    skills = openclaw.get("skills")
    output["skills"] = transform_skills(skills)
    if skills:
        logger.info(f"Transformed {len(output['skills'])} skill(s)")

    # Memory
    memory = openclaw.get("memory")
    output["memory"] = transform_memory(memory)
    if memory:
        logger.info("Transformed memory configuration")

    return output


def dump_yaml(data: Dict[str, Any], path: str) -> None:
    """Dump transformed config to YAML file."""
    try:
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Wrote config to {path}")
    except Exception as e:
        logger.error(f"Failed to write output: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Transform hiclaw openclaw.json to Hermes config.yaml"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=os.getenv("HICLAW_OPENCLAW_PATH", "openclaw.json"),
        help="Input openclaw.json file (default: HICLAW_OPENCLAW_PATH or openclaw.json)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=os.getenv("HICLAW_CONFIG_PATH", "config.yaml"),
        help="Output config.yaml file (default: HICLAW_CONFIG_PATH or config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Ensure output directory exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load input
    logger.info(f"Loading input from {args.input}")
    openclaw = load_openclaw_json(args.input)

    # Transform
    logger.info("Transforming configuration...")
    hermes_config = transform_config(openclaw)

    # Dump output
    dump_yaml(hermes_config, args.output)
    logger.info("Transformation complete")


if __name__ == "__main__":
    main()

"""Spill large MCP tool results to chat-scoped JSON files.

When a response exceeds the configured threshold, the full payload is written under
``chats/{chat_id}/mcp/`` and the model receives a compact stub (shape, sample, path)
plus tools to fetch only the slices it needs. Spilled files are staged into
``/workspace/data/`` for ``code_execution`` — not cowork.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.services.mcp.client import truncate_json_text
from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

MCP_DATA_CONTAINER_DIR = "/workspace/data"
_MANIFEST_NAME = "manifest.json"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def spill_threshold_chars() -> int:
    return max(500, int(settings.mcp_spill_threshold_chars))


def spill_sample_chars() -> int:
    return max(200, int(settings.mcp_spill_sample_chars))


def spill_get_max_chars() -> int:
    return max(500, int(settings.mcp_spill_get_max_chars))


def _files_root() -> Path:
    return Path(settings.files_base_dir).resolve()


def mcp_spill_dir(chat_id: str) -> Path:
    return _files_root() / "chats" / str(chat_id) / "mcp"


def mcp_data_exec_path(file_name: str) -> str:
    return f"{MCP_DATA_CONTAINER_DIR}/{file_name}"


def serialized_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def describe_shape(value: Any, *, depth: int = 0, max_depth: int = 3) -> str:
    """Compact one-line schema for model stubs (not a full JSON Schema dump)."""
    if depth >= max_depth:
        if isinstance(value, dict):
            return f"object[{len(value)}]"
        if isinstance(value, list):
            return f"array[{len(value)}]"
        if isinstance(value, str):
            return "str"
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        return type(value).__name__

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value:
            return "array[0]"
        item = describe_shape(value[0], depth=depth + 1, max_depth=max_depth)
        # Spot-check a couple more items; fall back if shapes diverge.
        for extra in value[1:4]:
            if describe_shape(extra, depth=depth + 1, max_depth=max_depth) != item:
                return f"array[{len(value)}] of mixed"
        return f"array[{len(value)}] of {item}"
    if isinstance(value, dict):
        if not value:
            return "object{}"
        keys = list(value.keys())
        # Prefer insertion order for readability; cap how many we inspect.
        inspect = keys[:60]
        child_shapes = {
            str(k): describe_shape(value[k], depth=depth + 1, max_depth=max_depth)
            for k in inspect
        }
        unique = set(child_shapes.values())
        # Homogeneous map (e.g. date → same row schema): collapse instead of
        # repeating the full item schema per key.
        if len(keys) >= 4 and len(unique) == 1:
            examples = ", ".join(str(k) for k in keys[:2])
            more = f", +{len(keys) - 2} more" if len(keys) > 2 else ""
            return (
                f"object[{len(keys)} keys ({examples}{more}) → {next(iter(unique))}]"
            )
        parts: list[str] = []
        for key in keys[:12]:
            parts.append(f"{key}:{child_shapes[str(key)]}")
        if len(keys) > 12:
            parts.append(f"+{len(keys) - 12} more")
        return "{" + ", ".join(parts) + "}"
    return type(value).__name__


def sample_value(value: Any, *, max_chars: int | None = None) -> Any:
    budget = max_chars if max_chars is not None else spill_sample_chars()
    return truncate_json_text(_shallow_sample(value), max_chars=budget)


def _shallow_sample(value: Any, *, depth: int = 0) -> Any:
    if depth >= 2:
        if isinstance(value, str) and len(value) > 80:
            return value[:77] + "..."
        if isinstance(value, list):
            return f"<array len={len(value)}>"
        if isinstance(value, dict):
            return f"<object keys={len(value)}>"
        return value
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    if isinstance(value, list):
        return [_shallow_sample(item, depth=depth + 1) for item in value[:2]]
    if isinstance(value, dict):
        keys = list(value.keys())
        # Homogeneous maps: one example key is enough; schema covers the rest.
        if len(keys) >= 4:
            shapes = {
                describe_shape(value[k], depth=0, max_depth=2) for k in keys[:8]
            }
            if len(shapes) == 1:
                first = keys[0]
                return {
                    str(first): _shallow_sample(value[first], depth=depth + 1),
                    "_note": f"+{len(keys) - 1} more keys with the same shape",
                }
        return {
            str(k): _shallow_sample(v, depth=depth + 1)
            for k, v in list(value.items())[:6]
        }
    return value


def _safe_token(value: str, *, max_len: int = 48) -> str:
    cleaned = _SAFE_RE.sub("_", value).strip("._") or "mcp"
    return cleaned[:max_len]


def _manifest_path(chat_id: str) -> Path:
    return mcp_spill_dir(chat_id) / _MANIFEST_NAME


def _read_manifest(chat_id: str) -> list[dict[str, Any]]:
    path = _manifest_path(chat_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("artifacts"), list):
        return [item for item in data["artifacts"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _write_manifest(chat_id: str, artifacts: list[dict[str, Any]]) -> None:
    path = _manifest_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"artifacts": artifacts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_spilled_artifacts(chat_id: str) -> list[dict[str, Any]]:
    return list(_read_manifest(chat_id))


def load_spilled_payload(chat_id: str, artifact_id: str) -> dict[str, Any] | None:
    artifact_id = str(artifact_id or "").strip()
    if not artifact_id:
        return None
    for entry in _read_manifest(chat_id):
        if str(entry.get("artifact_id")) != artifact_id:
            continue
        file_name = str(entry.get("file_name") or "")
        if not file_name:
            return None
        path = mcp_spill_dir(chat_id) / file_name
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed reading MCP spill %s: %s", path, exc)
            return None
        if isinstance(data, dict):
            return data
        return {"data": data}
    return None


def resolve_json_path(data: Any, path: str | None) -> Any:
    """Resolve a simple dotted path with optional ``[index]`` / ``[start:end]``."""
    if not path or not str(path).strip():
        return data
    current = data
    for raw_seg in str(path).strip().split("."):
        seg = raw_seg.strip()
        if not seg:
            continue
        name = seg
        brackets: list[str] = []
        while "[" in name and name.endswith("]"):
            open_at = name.rfind("[")
            brackets.insert(0, name[open_at + 1 : -1])
            name = name[:open_at]
        if name:
            if not isinstance(current, dict):
                raise KeyError(f"Cannot access key {name!r} on {type(current).__name__}")
            if name not in current:
                raise KeyError(f"Missing key {name!r}")
            current = current[name]
        for bracket in brackets:
            if not isinstance(current, list):
                raise KeyError(f"Cannot index {type(current).__name__} with [{bracket}]")
            if ":" in bracket:
                start_s, _, end_s = bracket.partition(":")
                start = int(start_s) if start_s.strip() else None
                end = int(end_s) if end_s.strip() else None
                current = current[start:end]
            else:
                current = current[int(bracket)]
    return current


def spill_mcp_payload(
    payload: Any,
    *,
    chat_id: str,
    server_id: str,
    tool_name: str,
) -> dict[str, Any]:
    """Write full payload to disk and return a model-facing stub."""
    artifact_id = uuid4().hex[:12]
    file_name = (
        f"{_safe_token(server_id)}__{_safe_token(tool_name)}_{artifact_id}.json"
    )
    body = payload if isinstance(payload, (dict, list)) else {"value": payload}
    raw = json.dumps(body, ensure_ascii=False, default=str)
    abs_path = mcp_spill_dir(chat_id) / file_name
    root = _files_root()
    if not str(abs_path.resolve()).startswith(str(root)):
        raise ValueError("Invalid MCP spill path")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(raw, encoding="utf-8")

    shape = describe_shape(body)
    sample = sample_value(body)
    entry = {
        "artifact_id": artifact_id,
        "file_name": file_name,
        "server_id": server_id,
        "tool": tool_name,
        "bytes": len(raw.encode("utf-8")),
        "chars": len(raw),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "shape": shape,
        "path": mcp_data_exec_path(file_name),
    }
    artifacts = _read_manifest(chat_id)
    artifacts.append(entry)
    # Keep manifest bounded for long chats.
    if len(artifacts) > 200:
        artifacts = artifacts[-200:]
    _write_manifest(chat_id, artifacts)

    return {
        "stored": True,
        "is_error": bool(isinstance(payload, dict) and payload.get("is_error")),
        "artifact_id": artifact_id,
        "file_name": file_name,
        "path": mcp_data_exec_path(file_name),
        "bytes": entry["bytes"],
        "chars": entry["chars"],
        "server_id": server_id,
        "tool": tool_name,
        "shape": shape,
        "sample": sample,
        "hint": "Use mcp_data_get for slices, or code_execution on path.",
    }


def maybe_spill_mcp_output(
    output: Any,
    *,
    chat_id: str | None,
    server_id: str,
    tool_name: str,
) -> Any:
    """Spill oversized MCP outputs; otherwise return as-is (soft string truncate)."""
    if isinstance(output, dict) and (output.get("is_error") or output.get("error")):
        return truncate_json_text(output, max_chars=settings.mcp_max_result_chars)

    size = serialized_size(output)
    threshold = spill_threshold_chars()
    if chat_id and size > threshold:
        try:
            return spill_mcp_payload(
                output,
                chat_id=str(chat_id),
                server_id=server_id,
                tool_name=tool_name,
            )
        except Exception as exc:
            logger.warning(
                "MCP spill failed server=%s tool=%s: %s",
                server_id,
                tool_name,
                exc,
                exc_info=True,
            )
            return truncate_json_text(output, max_chars=settings.mcp_max_result_chars)
    return truncate_json_text(output, max_chars=settings.mcp_max_result_chars)


def stage_mcp_data_for_exec(chat_id: str, work_dir: Path) -> list[dict[str, Any]]:
    """Copy spilled MCP JSON into ``work_dir/data`` for ``/workspace/data``."""
    source = mcp_spill_dir(chat_id)
    target = work_dir / "data"
    staged: list[dict[str, Any]] = []
    if not source.is_dir():
        return staged
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.json")):
        if path.name == _MANIFEST_NAME:
            dest = target / path.name
            dest.write_bytes(path.read_bytes())
            continue
        dest = target / path.name
        dest.write_bytes(path.read_bytes())
        staged.append(
            {
                "file_name": path.name,
                "path": mcp_data_exec_path(path.name),
                "bytes": dest.stat().st_size,
            }
        )
    return staged


def register_mcp_data_tools(registry: ToolRegistry, *, chat_id: str) -> None:
    """Tools to list and slice spilled MCP JSON without loading everything into context."""

    async def _list_handler(_args: dict) -> ToolResult:
        artifacts = list_spilled_artifacts(chat_id)
        return ToolResult(
            name="mcp_data_list",
            output={
                "count": len(artifacts),
                "artifacts": [
                    {
                        "artifact_id": a.get("artifact_id"),
                        "file_name": a.get("file_name"),
                        "server_id": a.get("server_id"),
                        "tool": a.get("tool"),
                        "bytes": a.get("bytes"),
                        "path": a.get("path"),
                        "created_at": a.get("created_at"),
                        "shape": a.get("shape"),
                    }
                    for a in artifacts
                ],
                "code_execution_dir": MCP_DATA_CONTAINER_DIR,
            },
        )

    async def _get_handler(args: dict) -> ToolResult:
        artifact_id = str((args or {}).get("artifact_id") or "").strip()
        if not artifact_id:
            return ToolResult(
                name="mcp_data_get",
                output={"error": "artifact_id is required"},
            )
        payload = load_spilled_payload(chat_id, artifact_id)
        if payload is None:
            return ToolResult(
                name="mcp_data_get",
                output={"error": f"Unknown or missing artifact_id={artifact_id}"},
            )
        path = (args or {}).get("path")
        path_s = str(path).strip() if path is not None else ""
        try:
            selected = resolve_json_path(payload, path_s or None)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            return ToolResult(
                name="mcp_data_get",
                output={
                    "error": f"path resolution failed: {exc}",
                    "artifact_id": artifact_id,
                    "path": path_s or None,
                    "shape": describe_shape(payload),
                },
            )

        # Optional array window when path lands on a list.
        offset = (args or {}).get("offset")
        limit = (args or {}).get("limit")
        if isinstance(selected, list) and (offset is not None or limit is not None):
            try:
                start = int(offset) if offset is not None else 0
            except (TypeError, ValueError):
                start = 0
            try:
                take = int(limit) if limit is not None else len(selected)
            except (TypeError, ValueError):
                take = len(selected)
            take = max(0, min(take, 500))
            start = max(0, start)
            selected = selected[start : start + take]
            window = {"offset": start, "limit": take, "returned": len(selected)}
        else:
            window = None

        max_chars = spill_get_max_chars()
        size = serialized_size(selected)
        if size > max_chars:
            return ToolResult(
                name="mcp_data_get",
                output={
                    "artifact_id": artifact_id,
                    "path": path_s or None,
                    "truncated": True,
                    "chars": size,
                    "max_chars": max_chars,
                    "shape": describe_shape(selected),
                    "sample": sample_value(selected, max_chars=min(2000, max_chars)),
                    "hint": (
                        "Slice is still large. Narrow `path`, use offset/limit on arrays, "
                        "or load the file in code_execution."
                    ),
                    "window": window,
                },
            )
        return ToolResult(
            name="mcp_data_get",
            output={
                "artifact_id": artifact_id,
                "path": path_s or None,
                "chars": size,
                "window": window,
                "data": selected,
            },
        )

    registry.register(
        ToolSpec(
            name="mcp_data_list",
            description=(
                "List MCP tool results spilled to disk for this chat "
                "(large responses stored as JSON under /workspace/data/). "
                "Use before mcp_data_get or code_execution."
            ),
            parameters={"type": "object", "properties": {}},
        ),
        _list_handler,
    )
    registry.register(
        ToolSpec(
            name="mcp_data_get",
            description=(
                "Fetch a slice of a spilled MCP JSON artifact. "
                "Use `path` for dotted access with optional [index] or [start:end], "
                "e.g. data.items[0:20]. Prefer this over loading the whole file into context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "Id from mcp_data_list or a stored MCP tool stub",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional JSON path, e.g. data.records[0] or data.items[0:10]"
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Start index when the resolved value is an array",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max array items to return (capped)",
                    },
                },
                "required": ["artifact_id"],
            },
        ),
        _get_handler,
    )

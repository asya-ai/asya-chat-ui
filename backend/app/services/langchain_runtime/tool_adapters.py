from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, create_model
from langchain_core.tools import StructuredTool

from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


def _safe_model_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]", "", (name or "Tool").replace("-", "_"))
    if cleaned and cleaned[0].isdigit():
        cleaned = f"Tool{cleaned}"
    return cleaned or "ToolArgs"


def _safe_field_name(key: str, *, used: set[str]) -> str:
    """Pydantic forbids leading underscores; identifiers must be valid Python names."""
    raw = str(key or "field")
    candidate = re.sub(r"[^0-9A-Za-z_]", "_", raw)
    if candidate.startswith("_"):
        candidate = f"field{candidate}"
    if not candidate or not candidate.isidentifier() or candidate[0].isdigit():
        candidate = f"field_{candidate}" if candidate else "field"
    base = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _json_schema_to_model(
    name: str, schema: dict[str, Any]
) -> tuple[type[BaseModel], dict[str, str]]:
    """Build a Pydantic args model and a map of safe_field_name -> original schema key."""
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    field_map: dict[str, str] = {}
    used: set[str] = set()
    for key, meta in properties.items():
        if not isinstance(meta, dict):
            continue
        safe_key = _safe_field_name(str(key), used=used)
        field_map[safe_key] = str(key)
        type_name = str(meta.get("type") or "string")
        annotation: Any = str
        default: Any = ...
        if type_name == "number":
            annotation = float
        elif type_name == "integer":
            annotation = int
        elif type_name == "boolean":
            annotation = bool
        elif type_name == "array":
            annotation = list[Any]
        elif type_name == "object":
            annotation = dict[str, Any]
        if key not in required:
            default = None
            annotation = annotation | None
        description = meta.get("description")
        fields[safe_key] = (annotation, Field(default=default, description=description))
    if not fields:
        fields["input"] = (dict[str, Any] | None, Field(default=None))
    model = create_model(f"{_safe_model_name(name)}Args", **fields)  # type: ignore[arg-type]
    return model, field_map


class LangChainToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._tools: dict[str, StructuredTool] = {}
        # safe_name -> original schema key, per tool
        self._field_maps: dict[str, dict[str, str]] = {}
        for spec in registry.list_specs():
            self._tools[spec.name] = self._create_tool(spec)

    def list_specs(self) -> list[ToolSpec]:
        return self.registry.list_specs()

    def list_langchain_tools(self) -> list[StructuredTool]:
        return list(self._tools.values())

    def _to_schema_args(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Map provider/MCP original keys onto sanitized Pydantic field names."""
        field_map = self._field_maps.get(name) or {}
        if not field_map:
            return dict(arguments or {})
        original_to_safe = {original: safe for safe, original in field_map.items()}
        mapped: dict[str, Any] = {}
        for key, value in (arguments or {}).items():
            mapped[original_to_safe.get(key, key)] = value
        return mapped

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, output={"error": f"Tool not found: {name}"})
        try:
            result = await tool.ainvoke(self._to_schema_args(name, arguments or {}))
        except ValidationError as exc:
            logger.warning("Tool arg validation failed name=%s: %s", name, exc)
            return ToolResult(
                name=name,
                output={"error": f"Invalid tool arguments: {exc}"},
            )
        except Exception as exc:
            logger.warning("Tool execute failed name=%s: %s", name, exc, exc_info=True)
            return ToolResult(
                name=name,
                output={"error": f"{type(exc).__name__}: {exc}"},
            )
        if isinstance(result, ToolResult):
            if not isinstance(result.output, dict):
                return ToolResult(
                    name=result.name or name,
                    output={
                        "error": "Tool returned non-object output",
                        "raw": result.output,
                    },
                    attachments=result.attachments,
                )
            return result
        if isinstance(result, dict):
            return ToolResult(name=name, output=result)
        return ToolResult(
            name=name,
            output={
                "error": f"Unexpected tool return type: {type(result).__name__}",
                "raw": str(result)[:500],
            },
        )

    def _create_tool(self, spec: ToolSpec) -> StructuredTool:
        args_model, field_map = _json_schema_to_model(spec.name, spec.parameters)
        self._field_maps[spec.name] = field_map

        async def _run(**kwargs: Any) -> ToolResult:
            normalized = {
                field_map.get(key, key): value
                for key, value in kwargs.items()
                if value is not None
            }
            if spec.name == "code_execution" and "language" not in normalized:
                normalized["language"] = "python"
            return await self.registry.execute(spec.name, normalized)

        return StructuredTool(
            name=spec.name,
            description=spec.description,
            args_schema=args_model,
            coroutine=_run,
        )

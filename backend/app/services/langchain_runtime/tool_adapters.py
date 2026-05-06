from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool

from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec


def _json_schema_to_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    for key, meta in properties.items():
        if not isinstance(meta, dict):
            continue
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
        fields[key] = (annotation, Field(default=default, description=description))
    if not fields:
        fields["input"] = (dict[str, Any] | None, Field(default=None))
    return create_model(f"{name}Args", **fields)  # type: ignore[arg-type]


class LangChainToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._tools: dict[str, StructuredTool] = {}
        for spec in registry.list_specs():
            self._tools[spec.name] = self._create_tool(spec)

    def list_specs(self) -> list[ToolSpec]:
        return self.registry.list_specs()

    def list_langchain_tools(self) -> list[StructuredTool]:
        return list(self._tools.values())

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Tool not found: {name}")
        return await tool.ainvoke(arguments)

    def _create_tool(self, spec: ToolSpec) -> StructuredTool:
        args_model = _json_schema_to_model(spec.name.title().replace("_", ""), spec.parameters)

        async def _run(**kwargs: Any) -> ToolResult:
            normalized = {key: value for key, value in kwargs.items() if value is not None}
            if spec.name == "code_execution" and "language" not in normalized:
                normalized["language"] = "python"
            return await self.registry.execute(spec.name, normalized)

        return StructuredTool(
            name=spec.name,
            description=spec.description,
            args_schema=args_model,
            coroutine=_run,
        )

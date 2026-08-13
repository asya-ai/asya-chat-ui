import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolResult:
    name: str
    output: dict[str, Any]
    attachments: list[dict[str, Any]] | None = None


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult]]

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._tools[spec.name] = (spec, handler)

    def list_specs(self) -> list[ToolSpec]:
        return [tool[0] for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self._tools:
            return ToolResult(
                name=name,
                output={"error": f"Tool not found: {name}"},
            )
        spec, handler = self._tools[name]
        logger.info(
            "Tool start name=%s args_keys=%s", name, list(arguments.keys())
        )
        try:
            result = await handler(arguments)
        except Exception as exc:
            logger.warning("Tool handler failed name=%s: %s", name, exc, exc_info=True)
            return ToolResult(
                name=name,
                output={"error": f"{type(exc).__name__}: {exc}"},
            )
        if not isinstance(result, ToolResult):
            logger.warning(
                "Tool handler returned non-ToolResult name=%s type=%s",
                name,
                type(result).__name__,
            )
            return ToolResult(
                name=name,
                output={
                    "error": (
                        f"Tool handler returned {type(result).__name__} "
                        "instead of ToolResult"
                    ),
                    "raw": str(result)[:500],
                },
            )
        if not isinstance(result.output, dict):
            result = ToolResult(
                name=result.name or name,
                output={"error": "Tool returned non-object output", "raw": result.output},
                attachments=result.attachments,
            )
        output_keys = list(result.output.keys())
        logger.info(
            "Tool done name=%s output_keys=%s has_attachments=%s",
            name,
            output_keys,
            bool(result.attachments),
        )
        return result

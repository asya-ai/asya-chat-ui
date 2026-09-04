"""Backward-compatible re-exports for MCP server configuration types."""

from app.services.mcp.types import McpInclude, McpServerConfig

__all__ = ["McpInclude", "McpServerConfig"]

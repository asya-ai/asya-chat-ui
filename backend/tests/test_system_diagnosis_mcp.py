"""MCP panel of system diagnosis: instance + viewing user's connections."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.mcp.types import McpInclude, McpServerConfig, ResolvedMcpServer
from app.services.system_diagnosis import _collect_mcp_servers


def _config(slug: str, *, url: str = "https://example.com/mcp") -> McpServerConfig:
    return McpServerConfig(
        id=slug,
        name=slug,
        transport="http",
        url=url,
        include=McpInclude(),
    )


def test_collect_mcp_servers_probes_resolved_and_marks_unconnected() -> None:
    user_id = uuid4()
    org_id = uuid4()
    shared = _config("shared-mcp")
    outlook = _config(
        "catalog-outlook",
        url="http://127.0.0.1:8000/internal/graph-mcp/mcp",
    )
    outlook = replace(outlook, headers={"Authorization": "Bearer tok"})

    connected = ResolvedMcpServer(
        db_id=uuid4(),
        config=outlook,
        auth_type="user_provided",
        auth_fingerprint="fp",
    )

    instance_shared = MagicMock()
    instance_shared.slug = "shared-mcp"
    instance_shared.name = "Shared"
    instance_shared.transport = "http"
    instance_shared.auth_type = "none"
    instance_shared.auth_config = {}
    instance_shared.catalog_id = None
    instance_shared.url = "https://example.com/mcp"

    instance_todo = MagicMock()
    instance_todo.slug = "catalog-todo"
    instance_todo.name = "Microsoft To Do"
    instance_todo.transport = "http"
    instance_todo.auth_type = "user_provided"
    instance_todo.auth_config = {}
    instance_todo.catalog_id = "todo"
    instance_todo.url = "http://127.0.0.1:8000/internal/graph-mcp/mcp"

    membership = MagicMock(org_id=org_id)

    session = MagicMock()
    # OrgMemberships, then instance rows
    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[membership])),
        MagicMock(all=MagicMock(return_value=[instance_shared, instance_todo])),
    ]

    session_cm = MagicMock()
    session_cm.__enter__.return_value = session
    session_cm.__exit__.return_value = False

    def fake_probe(config: McpServerConfig):
        from app.services.system_diagnosis import McpServerCheck

        return McpServerCheck(
            id=config.id,
            name=config.name,
            transport=config.transport,
            status="ok",
            tools=3,
            resources=0,
            prompts=0,
            detail="3 tools, 0 resources, 0 prompts",
            latency_ms=1.0,
        )

    with (
        patch(
            "app.db.session.SessionLocal",
            return_value=session_cm,
        ),
        patch(
            "app.services.mcp.registry.resolve_effective_mcp_servers",
            return_value=[connected],
        ),
        patch(
            "app.services.system_diagnosis._mcp_config_for_instance_row",
            side_effect=[
                # shared already seen via resolve? not in resolved — probe path
                (shared, None),
                (None, "Not connected for you"),
            ],
        ),
        patch(
            "app.services.system_diagnosis._probe_mcp_server",
            side_effect=fake_probe,
        ),
    ):
        # resolve returns outlook only; shared + todo handled in instance loop
        checks = _collect_mcp_servers(user_id)

    by_id = {c.id: c for c in checks}
    assert by_id["catalog-outlook"].status == "ok"
    assert by_id["catalog-outlook"].tools == 3
    assert by_id["shared-mcp"].status == "ok"
    assert by_id["catalog-todo"].status == "missing"
    assert by_id["catalog-todo"].detail == "Not connected for you"

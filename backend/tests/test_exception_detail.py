from __future__ import annotations

import pytest

from app.core.exceptions import format_exception_detail


def test_format_exception_detail_simple() -> None:
    assert format_exception_detail(ValueError("bad input")) == "ValueError: bad input"


def test_format_exception_detail_unwraps_exception_group() -> None:
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [ConnectionError("All connection attempts failed")],
    )
    detail = format_exception_detail(group)
    assert "TaskGroup" not in detail
    assert "ConnectionError" in detail
    assert "All connection attempts failed" in detail


def test_format_exception_detail_follows_cause_chain() -> None:
    root = ConnectionRefusedError("Connection refused")
    wrapped = RuntimeError("MCP session failed")
    wrapped.__cause__ = root
    detail = format_exception_detail(wrapped)
    assert "ConnectionRefusedError" in detail
    assert "Connection refused" in detail


def test_format_exception_detail_nested_group_in_cause() -> None:
    leaf = TimeoutError("timed out reading response")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [leaf])
    outer = RuntimeError("MCP discovery failed")
    outer.__cause__ = group
    detail = format_exception_detail(outer)
    assert "TaskGroup" not in detail
    assert "TimeoutError" in detail
    assert "timed out reading response" in detail


@pytest.mark.parametrize(
    "exc",
    [
        ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [OSError(61, "Connection refused")]),
    ],
)
def test_format_exception_detail_matches_diagnosis_noise(exc: BaseException) -> None:
    detail = format_exception_detail(exc)
    assert detail != "unhandled errors in a TaskGroup (1 sub-exception)"
    assert "Connection refused" in detail or "OSError" in detail

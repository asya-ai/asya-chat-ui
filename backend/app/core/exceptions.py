from __future__ import annotations

from typing import Any


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten ExceptionGroup trees into concrete leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for sub in exc.exceptions:
            leaves.extend(_leaf_exceptions(sub))
        return leaves or [exc]
    return [exc]


def _root_cause(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nxt = current.__cause__
        if nxt is None and not getattr(current, "__suppress_context__", False):
            nxt = current.__context__
        if nxt is None or nxt is current:
            break
        # Prefer a concrete sub-exception over a TaskGroup wrapper.
        if isinstance(nxt, BaseExceptionGroup):
            leaves = _leaf_exceptions(nxt)
            current = leaves[0] if leaves else nxt
            continue
        current = nxt
    if isinstance(current, BaseExceptionGroup):
        leaves = _leaf_exceptions(current)
        return leaves[0] if leaves else current
    return current


def _one_line(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = " ".join(str(exc).split()).strip()
    if not msg or msg == name:
        return name
    if "TaskGroup" in msg and "sub-exception" in msg:
        # Never surface the opaque TaskGroup summary alone.
        leaves = _leaf_exceptions(exc) if isinstance(exc, BaseExceptionGroup) else []
        if leaves and leaves[0] is not exc:
            return _one_line(leaves[0])
        return name
    return f"{name}: {msg}"


def format_exception_detail(exc: BaseException, *, limit: int = 320) -> str:
    """Human-readable exception text; unwrap ExceptionGroup / cause chains."""
    roots: list[BaseException] = []
    for leaf in _leaf_exceptions(exc):
        roots.append(_root_cause(leaf))

    seen: set[str] = set()
    parts: list[str] = []
    for item in roots:
        text = _one_line(item)
        if text in seen:
            continue
        seen.add(text)
        parts.append(text)

    if not parts:
        parts.append(_one_line(exc))

    joined = " | ".join(parts)
    if len(joined) <= limit:
        return joined
    return f"{joined[: limit - 3]}..."


def exception_to_str(exc: Any, *, limit: int = 320) -> str:
    if isinstance(exc, BaseException):
        return format_exception_detail(exc, limit=limit)
    return format_exception_detail(RuntimeError(str(exc)), limit=limit)

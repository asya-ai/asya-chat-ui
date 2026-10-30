"""Docker sandbox per generation session — shared workspace dirs, bounded holder containers."""

from __future__ import annotations

import concurrent.futures
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import anyio
from docker.errors import APIError, ImageNotFound, NotFound
from docker.types import Ulimit

from app.core.config import settings

logger = logging.getLogger(__name__)

_SESSIONS: dict[str, "_ExecSession"] = {}
_REGISTRY_LOCK = threading.Lock()


@dataclass(frozen=True)
class ExecSessionPaths:
    inputs_dir: Path
    work_dir: Path
    outputs_dir: Path
    host_inputs_dir: Path
    host_work_dir: Path
    host_outputs_dir: Path


@dataclass
class _ExecSession:
    session_key: str
    paths: ExecSessionPaths
    lock: anyio.Lock
    container_id: str | None = None


def exec_session_key(*, generation_task_id: str | None, exec_session_id: str | None) -> str:
    if generation_task_id:
        return f"task:{generation_task_id}"
    if exec_session_id:
        return f"stream:{exec_session_id}"
    raise ValueError("generation_task_id or exec_session_id is required for session sandbox")


def _holder_sleep_seconds(*, exec_timeout_seconds: int) -> int:
    cap = max(30, int(settings.exec_session_container_seconds))
    # Holder must outlive a single exec; still bounded so crashed workers cannot leak forever.
    return min(cap, max(60, int(exec_timeout_seconds) + 45))


def prepare_session_paths(filespace_id: str, session_key: str) -> ExecSessionPaths:
    if not settings.exec_host_files_dir:
        raise ValueError("EXEC_HOST_FILES_DIR is not configured.")
    container_base = Path(settings.files_base_dir)
    host_base = Path(settings.exec_host_files_dir)
    rel_path = Path("chats") / filespace_id / "exec" / "sessions" / session_key.replace(":", "_")
    host_inputs_dir = host_base / rel_path / "inputs"
    host_work_dir = host_base / rel_path / "work"
    host_outputs_dir = host_base / rel_path / "outputs"
    # Docker bind mounts use EXEC_HOST_FILES_DIR; stage files there (not only FILES_BASE_DIR).
    for path in (host_inputs_dir, host_work_dir, host_outputs_dir):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o777)
    if host_base.resolve() != container_base.resolve():
        for path in (
            container_base / rel_path / "inputs",
            container_base / rel_path / "work",
            container_base / rel_path / "outputs",
        ):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o777)
    return ExecSessionPaths(
        inputs_dir=host_inputs_dir,
        work_dir=host_work_dir,
        outputs_dir=host_outputs_dir,
        host_inputs_dir=host_inputs_dir,
        host_work_dir=host_work_dir,
        host_outputs_dir=host_outputs_dir,
    )


def _get_or_create_session(session_key: str, paths: ExecSessionPaths) -> _ExecSession:
    with _REGISTRY_LOCK:
        existing = _SESSIONS.get(session_key)
        if existing is not None:
            existing.paths = paths
            return existing
        session = _ExecSession(session_key=session_key, paths=paths, lock=anyio.Lock())
        _SESSIONS[session_key] = session
        return session


def refresh_inputs_dir(inputs_dir: Path) -> None:
    if not inputs_dir.exists():
        inputs_dir.mkdir(parents=True, exist_ok=True)
        inputs_dir.chmod(0o777)
        return
    for child in inputs_dir.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed clearing exec input %s: %s", child, exc)


def _clear_outputs_dir(outputs_dir: Path) -> None:
    if not outputs_dir.exists():
        return
    for child in outputs_dir.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed clearing exec output %s: %s", child, exc)


def _clear_dir_contents(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed clearing exec dir entry %s: %s", child, exc)


def invalidate_exec_session(
    session_key: str,
    *,
    filespace_id: str | None = None,
) -> None:
    """Drop container and registry entry (edit branch, checkpoint resume, etc.)."""
    with _REGISTRY_LOCK:
        session = _SESSIONS.pop(session_key, None)
    paths = session.paths if session is not None else None
    if session is not None:
        _stop_container(session.container_id)
        session.container_id = None
    _stop_container_by_label(session_key)
    if paths is None and filespace_id:
        try:
            paths = prepare_session_paths(filespace_id, session_key)
        except ValueError:
            paths = None
    if paths is not None:
        _clear_dir_contents(paths.work_dir)
        _clear_dir_contents(paths.outputs_dir)
        refresh_inputs_dir(paths.inputs_dir)


def close_exec_session(session_key: str, *, filespace_id: str | None = None) -> None:
    invalidate_exec_session(session_key, filespace_id=filespace_id)


def purge_leaked_exec_containers() -> int:
    """Remove exited holders and running session containers past TTL."""
    import docker

    try:
        client = docker.from_env()
    except Exception as exc:
        logger.debug("Exec container purge skipped: %s", exc)
        return 0

    removed = 0
    ttl = max(30, int(settings.exec_session_container_seconds))
    now = int(time.time())
    try:
        containers = client.containers.list(
            all=True, filters={"label": "chatui.exec=true"},
        )
        for container in containers:
            labels = container.labels or {}
            if labels.get("chatui.exec.session") is None:
                if container.status == "exited":
                    try:
                        container.remove(force=True)
                        removed += 1
                    except Exception:
                        pass
                continue

            created_raw = labels.get("chatui.exec.created_at")
            try:
                created = int(created_raw) if created_raw else None
            except (TypeError, ValueError):
                created = None

            expired = created is not None and (now - created) >= ttl
            if container.status == "exited" or expired:
                try:
                    if container.status == "running":
                        container.kill()
                    container.remove(force=True)
                    removed += 1
                except Exception as exc:
                    logger.debug(
                        "Exec container purge failed id=%s: %s",
                        container.short_id,
                        exc,
                    )
    except Exception as exc:
        logger.warning("Exec container purge failed: %s", exc)
    finally:
        try:
            client.close()
        except Exception:
            pass
    if removed:
        logger.info("Purged leaked exec containers count=%s", removed)
    return removed


def _stop_container_by_label(session_key: str) -> None:
    import docker

    try:
        client = docker.from_env()
    except Exception:
        return
    try:
        containers = client.containers.list(
            all=True,
            filters={"label": f"chatui.exec.session={session_key}"},
        )
        for container in containers:
            _stop_container(container.id, client=client)
    except Exception as exc:
        logger.debug("Exec session label cleanup failed key=%s: %s", session_key, exc)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _stop_container(container_id: str | None, *, client=None) -> None:
    if not container_id:
        return
    owns_client = client is None
    if client is None:
        import docker

        client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        try:
            container.kill()
        except Exception:
            pass
        try:
            container.remove(force=True)
        except (NotFound, APIError):
            pass
    except NotFound:
        pass
    except Exception as exc:
        logger.warning("Failed stopping exec container id=%s: %s", container_id, exc)
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:
                pass


def _host_config(client, *, timeout_seconds: int, volumes: dict) -> dict:
    memory_limit = settings.exec_memory_limit
    ulimits = [
        Ulimit(
            name="nofile",
            soft=settings.exec_ulimit_nofile,
            hard=settings.exec_ulimit_nofile,
        ),
        Ulimit(
            name="nproc",
            soft=settings.exec_ulimit_nproc,
            hard=settings.exec_ulimit_nproc,
        ),
        Ulimit(
            name="fsize",
            soft=settings.exec_ulimit_fsize_bytes,
            hard=settings.exec_ulimit_fsize_bytes,
        ),
        Ulimit(
            name="cpu",
            soft=max(1, timeout_seconds),
            hard=max(1, timeout_seconds),
        ),
    ]
    from app.services.tools.code_execution import _EXEC_MASKED_PATHS

    host_config = client.api.create_host_config(
        binds=volumes,
        network_mode="none",
        nano_cpus=max(1, int(settings.exec_cpu_limit * 1e9)),
        mem_limit=memory_limit,
        memswap_limit=memory_limit,
        pids_limit=max(1, settings.exec_pids_limit),
        ulimits=ulimits,
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        tmpfs={
            "/tmp": (
                f"rw,nosuid,nodev,size={settings.exec_tmpfs_size},"
                "mode=1777"
            ),
        },
    )
    host_config["MaskedPaths"] = list(_EXEC_MASKED_PATHS)
    return host_config


def _container_holder_expired(container, *, ttl: int, now: int) -> bool:
    labels = container.labels or {}
    created_raw = labels.get("chatui.exec.created_at")
    try:
        created = int(created_raw) if created_raw else None
    except (TypeError, ValueError):
        created = None
    if created is None:
        return True
    return (now - created) >= ttl


def _ensure_running_container(
    session: _ExecSession,
    *,
    timeout_seconds: int,
    build_image_if_missing,
) -> None:
    import docker

    purge_leaked_exec_containers()
    client = docker.from_env()
    ttl = max(30, int(settings.exec_session_container_seconds))
    now = int(time.time())
    sleep_seconds = _holder_sleep_seconds(exec_timeout_seconds=timeout_seconds)
    try:
        if session.container_id:
            try:
                container = client.containers.get(session.container_id)
                if container.status == "running" and not _container_holder_expired(
                    container, ttl=ttl, now=now
                ):
                    return
            except NotFound:
                pass
            session.container_id = None

        filters = {"label": f"chatui.exec.session={session.session_key}"}
        running = client.containers.list(filters={**filters, "status": "running"})
        if running and not _container_holder_expired(running[0], ttl=ttl, now=now):
            session.container_id = running[0].id
            return

        for stale in client.containers.list(all=True, filters=filters):
            _stop_container(stale.id, client=client)

        paths = session.paths
        volumes = {
            str(paths.host_inputs_dir): {"bind": "/inputs", "mode": "ro"},
            str(paths.host_work_dir): {"bind": "/workspace", "mode": "rw"},
            str(paths.host_outputs_dir): {"bind": "/outputs", "mode": "rw"},
        }
        host_config = _host_config(client, timeout_seconds=timeout_seconds, volumes=volumes)
        created_at = str(now)
        labels = {
            "chatui.exec": "true",
            "chatui.exec.session": session.session_key,
            "chatui.exec.created_at": created_at,
        }
        resp = client.api.create_container(
            settings.exec_docker_image,
            command=["sleep", str(sleep_seconds)],
            working_dir="/workspace",
            user="65534:65534",
            labels=labels,
            environment={"MPLCONFIGDIR": "/tmp/matplotlib", "HOME": "/tmp"},
            host_config=host_config,
            volumes=["/inputs", "/workspace", "/outputs"],
            network_disabled=True,
        )
        created = client.containers.get(resp["Id"])
        created.start()
        session.container_id = created.id
    except ImageNotFound:
        build_image_if_missing(client)
        _ensure_running_container(
            session, timeout_seconds=timeout_seconds, build_image_if_missing=build_image_if_missing
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


def _exec_bootstrap(
    container_id: str,
    *,
    timeout_seconds: int,
) -> tuple[str, str, int | None, bool]:
    import docker

    client = docker.from_env()
    timed_out = False
    exit_code: int | None = 1
    stdout = ""
    stderr = ""
    try:
        container = client.containers.get(container_id)

        def _run() -> tuple[int, tuple[bytes, bytes]]:
            return container.exec_run(
                ["python", "/workspace/_sandbox_bootstrap.py"],
                workdir="/workspace",
                user="65534:65534",
                demux=True,
                environment={"MPLCONFIGDIR": "/tmp/matplotlib", "HOME": "/tmp"},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            try:
                exit_code, output = future.result(timeout=max(1, timeout_seconds))
                out_chunks = output if isinstance(output, tuple) else (output, b"")
                stdout = (out_chunks[0] or b"").decode("utf-8", errors="replace")
                stderr = (out_chunks[1] or b"").decode("utf-8", errors="replace")
            except concurrent.futures.TimeoutError:
                timed_out = True
                exit_code = None
                try:
                    container.kill()
                except Exception:
                    pass
    finally:
        try:
            client.close()
        except Exception:
            pass
    return stdout, stderr, exit_code, timed_out


def _release_holder(session: _ExecSession) -> None:
    cid = session.container_id
    session.container_id = None
    if cid:
        _stop_container(cid)
    _stop_container_by_label(session.session_key)


async def run_in_exec_session(
    *,
    session_key: str,
    paths: ExecSessionPaths,
    timeout_seconds: int,
    build_image_if_missing,
) -> tuple[str, str, int | None, bool, dict[str, int]]:
    """Run main.py in a reused workspace; holder container is always torn down after."""
    session = _get_or_create_session(session_key, paths)
    timing: dict[str, int] = {}
    async with session.lock:
        _clear_outputs_dir(paths.outputs_dir)
        stdout = ""
        stderr = ""
        exit_code: int | None = 1
        timed_out = False
        try:
            def _phase_ensure() -> None:
                _ensure_running_container(
                    session,
                    timeout_seconds=timeout_seconds,
                    build_image_if_missing=build_image_if_missing,
                )

            t0 = anyio.current_time()
            await anyio.to_thread.run_sync(_phase_ensure)
            timing["container_ms"] = int((anyio.current_time() - t0) * 1000)

            if not session.container_id:
                raise RuntimeError("Exec session container unavailable")

            t1 = anyio.current_time()
            stdout, stderr, exit_code, timed_out = await anyio.to_thread.run_sync(
                _exec_bootstrap,
                session.container_id,
                timeout_seconds=timeout_seconds,
            )
            timing["exec_ms"] = int((anyio.current_time() - t1) * 1000)
        finally:
            await anyio.to_thread.run_sync(_release_holder, session)
            if timed_out:
                _stop_container_by_label(session_key)

    return stdout, stderr, exit_code, timed_out, timing

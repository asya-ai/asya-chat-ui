import base64
import json
import logging
import mimetypes
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import UUID, uuid4

import anyio
import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.types import Ulimit
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    ChatCoworkDocument,
    ChatMessage,
    ChatMessageAttachment,
)
from app.services.file_storage import maybe_read_file_bytes
from app.services.tools.cowork_tools import bump_content, document_payload, list_documents
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

ALLOWED_LANGUAGES = {"python"}
DEFAULT_ALLOWLIST = {
    "numpy",
    "pandas",
    "geopandas",
    "shapefile",
    "shapely",
    "matplotlib",
    "cv2",
    "seaborn",
    "scipy",
    "sklearn",
    "statsmodels",
    "PIL",
    "bs4",
    "lxml",
    "sympy",
    "openpyxl",
    "docx",
    "pptx",
    "reportlab",
    "odf",
    "pypandoc",
    "pypdf",
    "pdfplumber",
    "fitz",
    "pytesseract",
    "yaml",
    "dateutil",
    "click",
    "typer",
    "rich",
    "orjson",
    "pyarrow",
    "chardet",
    "charset_normalizer",
    "magic",
    "watchdog",
    "regex",
    "rapidfuzz",
    "duckdb",
    "networkx",
    "xarray",
    "polars",
}
ALLOWED_IMPORTS_HINT = ", ".join(sorted(DEFAULT_ALLOWLIST))
BLOCKED_IMPORTS = frozenset({"subprocess"})

# Container entrypoint: stub blocked stdlib, put cowork on path, then run user code.
_EXEC_BOOTSTRAP = f"""\
import runpy
import sys
import types

_COWORK_PATH = "/workspace/cowork"
if _COWORK_PATH not in sys.path:
    sys.path.append(_COWORK_PATH)


class _BlockedModule(types.ModuleType):
    def __getattr__(self, name):
        return self

    def __call__(self, *args, **kwargs):
        raise RuntimeError(f"{{self.__name__}} is not allowed in the sandbox")


for _name in {set(BLOCKED_IMPORTS)!r}:
    sys.modules[_name] = _BlockedModule(_name)

runpy.run_path("/workspace/main.py", run_name="__main__")
"""

# Docker replaces its default MaskedPaths when this is set — keep defaults + harden.
_EXEC_MASKED_PATHS = (
    # Docker / runc defaults
    "/proc/asound",
    "/proc/acpi",
    "/proc/kcore",
    "/proc/keys",
    "/proc/latency_stats",
    "/proc/timer_list",
    "/proc/timer_stats",
    "/proc/sched_debug",
    "/proc/scsi",
    "/sys/firmware",
    "/sys/devices/virtual/powercap",
    # Host fingerprinting / resource disclosure
    "/proc/cpuinfo",
    "/proc/meminfo",
    "/proc/stat",
    "/proc/version",
    "/proc/uptime",
    "/proc/loadavg",
    "/proc/cmdline",
    "/proc/devices",
    "/proc/diskstats",
    "/proc/partitions",
    "/proc/interrupts",
    "/proc/schedstat",
    "/proc/zoneinfo",
    "/proc/vmstat",
    "/proc/net",
    "/sys/fs/cgroup",
    "/sys/devices/system/cpu",
    "/sys/devices/system/node",
    "/sys/class/net",
    "/sys/class/dmi",
)


@dataclass
class CodeExecutionContext:
    session: Session
    org_id: str
    chat_id: str
    agent_id: str | None = None


def _collect_imports(code: str) -> set[str]:
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python code: {exc}") from exc
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.add(name.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def _auto_display_last_expr(code: str) -> str:
    """Wrap the last top-level bare expression in print(repr(...)) for REPL-like output."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    if not tree.body:
        return code
    last = tree.body[-1]
    if not isinstance(last, ast.Expr) or last.col_offset != 0:
        return code
    lines = code.split("\n")
    start = last.lineno - 1
    end = (last.end_lineno or last.lineno)
    expr_text = "\n".join(lines[start:end]).strip()
    replacement = (
        f"_result_ = {expr_text}\n"
        f"if _result_ is not None:\n"
        f"    print(repr(_result_))"
    )
    return "\n".join(lines[:start]) + "\n" + replacement + "\n" + "\n".join(lines[end:])


def _cowork_module_names(docs: Iterable[ChatCoworkDocument]) -> set[str]:
    """Importable module names for cowork ``*.py`` files staged under /workspace/cowork."""
    names: set[str] = set()
    used_names: set[str] = set()
    for doc in docs:
        safe = _sanitize_filename(doc.file_name or "document.txt")
        if safe.lower() in used_names or safe.lower() == "manifest.json":
            safe = f"{doc.id}_{safe}"
        used_names.add(safe.lower())
        if not safe.lower().endswith(".py"):
            continue
        stem = Path(safe).stem
        if stem.isidentifier():
            names.add(stem.lower())
    return names


def _validate_imports(code: str, extra_allowed: Iterable[str] | None = None) -> None:
    allowlist = {name.lower() for name in DEFAULT_ALLOWLIST}
    extra = {name.lower() for name in (extra_allowed or ())}
    stdlib = {name.lower() for name in sys.stdlib_module_names}
    for module in _collect_imports(code):
        base = module.split(".")[0].lower()
        if base in BLOCKED_IMPORTS:
            raise ValueError(f"Import not allowed: {module}.")
        if base in stdlib or base in allowlist or base in extra:
            continue
        raise ValueError(
            f"Import not allowed: {module}. "
            f"Allowed third-party imports: {ALLOWED_IMPORTS_HINT}"
        )


def _sanitize_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "file"


def _write_inputs(
    attachments: Iterable[ChatMessageAttachment], inputs_dir: Path
) -> list[dict]:
    inputs_dir.mkdir(parents=True, exist_ok=True)
    inputs: list[dict] = []
    total_bytes = 0
    for attachment in attachments:
        if len(inputs) >= settings.attachments_max_files:
            break
        safe_name = _sanitize_filename(attachment.file_name)
        filename = f"{attachment.id}_{safe_name}"
        payload: bytes | None = None
        if attachment.file_path:
            payload = maybe_read_file_bytes(attachment.file_path)
        if payload is None and attachment.data_base64:
            try:
                payload = base64.b64decode(attachment.data_base64)
            except Exception as exc:
                logger.warning(
                    "Failed to decode attachment id=%s filename=%s: %s",
                    attachment.id,
                    attachment.file_name,
                    exc,
                )
                continue
        if payload is None:
            continue
        if len(payload) > settings.attachments_max_file_bytes:
            logger.warning(
                "Skipping oversized attachment id=%s (%s bytes)",
                attachment.id,
                len(payload),
            )
            continue
        if total_bytes + len(payload) > settings.attachments_max_total_bytes:
            logger.warning(
                "Skipping attachment id=%s; total input size limit reached",
                attachment.id,
            )
            continue
        file_path = inputs_dir / filename
        try:
            file_path.write_bytes(payload)
        except Exception as exc:
            logger.warning(
                "Failed to write attachment id=%s filename=%s: %s",
                attachment.id,
                attachment.file_name,
                exc,
            )
            continue
        total_bytes += len(payload)
        inputs.append(
            {
                "name": attachment.file_name,
                "path": f"/inputs/{filename}",
                "content_type": attachment.content_type,
            }
        )
    return inputs


def _project_source_display_name(source: AgentSource) -> str:
    if source.file_name:
        return source.file_name
    title = (source.title or "source").strip() or "source"
    safe = _sanitize_filename(title)
    if "." in safe:
        return safe
    return f"{safe}.txt"


_TEXT_STANDIN_EXTENSIONS = {
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".md",
    ".markdown",
    ".py",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
}


def _text_standin_name(display_name: str) -> str:
    suffix = Path(display_name).suffix.lower()
    if suffix in _TEXT_STANDIN_EXTENSIONS:
        return display_name
    stem = Path(display_name).stem or "source"
    return f"{_sanitize_filename(stem)}.txt"


def project_source_exec_path(source: AgentSource) -> str:
    display_name = _project_source_display_name(source)
    if not source.file_path:
        display_name = _text_standin_name(display_name)
    safe_name = _sanitize_filename(display_name)
    return f"/inputs/project/{source.id}_{safe_name}"


def cowork_exec_path(doc: ChatCoworkDocument, *, safe_name: str | None = None) -> str:
    name = safe_name or _sanitize_filename(doc.file_name or "document.txt")
    return f"/workspace/cowork/{name}"


def _write_cowork_workspace(
    docs: Iterable[ChatCoworkDocument], work_dir: Path
) -> tuple[list[dict], dict[str, UUID], dict[str, str]]:
    """Seed /workspace/cowork with chat co-editing docs. Returns listing, path→id, snapshots."""
    cowork_dir = work_dir / "cowork"
    cowork_dir.mkdir(parents=True, exist_ok=True)
    try:
        cowork_dir.chmod(0o777)
    except OSError:
        pass

    listing: list[dict] = []
    path_to_id: dict[str, UUID] = {}
    snapshots: dict[str, str] = {}
    used_names: set[str] = set()

    for doc in docs:
        safe = _sanitize_filename(doc.file_name or "document.txt")
        if safe.lower() in used_names or safe.lower() == "manifest.json":
            safe = f"{doc.id}_{safe}"
        used_names.add(safe.lower())
        content = doc.content or ""
        file_path = cowork_dir / safe
        try:
            file_path.write_text(content, encoding="utf-8")
            file_path.chmod(0o666)
        except Exception as exc:
            logger.warning(
                "Failed to write cowork doc id=%s filename=%s: %s",
                doc.id,
                doc.file_name,
                exc,
            )
            continue
        container_path = cowork_exec_path(doc, safe_name=safe)
        path_to_id[safe] = doc.id
        snapshots[safe] = content
        fmt = doc.format.value if hasattr(doc.format, "value") else doc.format
        listing.append(
            {
                "document_id": str(doc.id),
                "title": doc.title,
                "file_name": doc.file_name,
                "format": fmt,
                "language": doc.language,
                "path": container_path,
                "is_active": bool(doc.is_active),
                "version": int(doc.version or 0),
            }
        )

    try:
        (cowork_dir / "manifest.json").write_text(
            json.dumps(listing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write cowork manifest: %s", exc)

    return listing, path_to_id, snapshots


def _sync_cowork_workspace(
    session: Session,
    chat_id: UUID,
    work_dir: Path,
    *,
    path_to_id: dict[str, UUID],
    snapshots: dict[str, str],
) -> list[dict]:
    """Persist text changes under /workspace/cowork back to ChatCoworkDocument rows."""
    cowork_dir = work_dir / "cowork"
    if not cowork_dir.is_dir() or not path_to_id:
        return []

    updated_payloads: list[dict] = []
    for safe_name, doc_id in path_to_id.items():
        file_path = cowork_dir / safe_name
        if not file_path.is_file():
            continue
        try:
            if file_path.stat().st_size > settings.attachments_max_file_bytes:
                logger.warning(
                    "Skipping oversized cowork sync id=%s (%s bytes)",
                    doc_id,
                    file_path.stat().st_size,
                )
                continue
            new_content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning(
                "Skipping non-text cowork sync id=%s filename=%s",
                doc_id,
                safe_name,
            )
            continue
        except Exception as exc:
            logger.warning(
                "Failed reading cowork sync id=%s filename=%s: %s",
                doc_id,
                safe_name,
                exc,
            )
            continue

        if new_content == snapshots.get(safe_name):
            continue

        doc = session.get(ChatCoworkDocument, doc_id)
        if not doc or doc.chat_id != chat_id:
            continue
        bump_content(doc, new_content, sync_assistant_snapshot=True)
        session.add(doc)
        session.commit()
        session.refresh(doc)
        payload = document_payload(doc)
        payload["status"] = "synced_from_code"
        payload["action"] = "update"
        payload["synced_path"] = f"/workspace/cowork/{safe_name}"
        updated_payloads.append(payload)

    return updated_payloads


def _write_project_inputs(
    sources: Iterable[AgentSource],
    inputs_dir: Path,
    *,
    used_bytes: int = 0,
    used_files: int = 0,
) -> list[dict]:
    project_dir = inputs_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    inputs: list[dict] = []
    total_bytes = used_bytes
    file_count = used_files
    for source in sources:
        if file_count >= settings.attachments_max_files:
            break
        display_name = _project_source_display_name(source)
        payload: bytes | None = None
        content_type = source.content_type or "text/plain"
        if source.file_path:
            payload = maybe_read_file_bytes(source.file_path)
        if payload is None and source.content_text:
            payload = source.content_text.encode("utf-8")
            display_name = _text_standin_name(display_name)
            if Path(display_name).suffix.lower() not in {".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".yaml", ".yml", ".md", ".markdown"}:
                content_type = "text/plain"
            elif not content_type:
                content_type = "text/plain"
        if payload is None:
            continue
        safe_name = _sanitize_filename(display_name)
        filename = f"{source.id}_{safe_name}"
        if len(payload) > settings.attachments_max_file_bytes:
            logger.warning(
                "Skipping oversized project source id=%s (%s bytes)",
                source.id,
                len(payload),
            )
            continue
        if total_bytes + len(payload) > settings.attachments_max_total_bytes:
            logger.warning(
                "Skipping project source id=%s; total input size limit reached",
                source.id,
            )
            continue
        file_path = project_dir / filename
        try:
            file_path.write_bytes(payload)
        except Exception as exc:
            logger.warning(
                "Failed to write project source id=%s filename=%s: %s",
                source.id,
                display_name,
                exc,
            )
            continue
        total_bytes += len(payload)
        file_count += 1
        inputs.append(
            {
                "name": display_name,
                "path": f"/inputs/project/{filename}",
                "content_type": content_type,
                "source_id": str(source.id),
                "title": source.title,
            }
        )
    return inputs


def _prepare_run_dirs(chat_id: str) -> tuple[Path, Path, Path, Path, Path, Path]:
    if not settings.exec_host_files_dir:
        raise ValueError("EXEC_HOST_FILES_DIR is not configured.")
    run_id = str(uuid4())
    container_base = Path(settings.files_base_dir)
    host_base = Path(settings.exec_host_files_dir)
    rel_path = Path("chats") / chat_id / "exec" / run_id
    inputs_dir = container_base / rel_path / "inputs"
    work_dir = container_base / rel_path / "work"
    outputs_dir = container_base / rel_path / "outputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    host_inputs_dir = host_base / rel_path / "inputs"
    host_work_dir = host_base / rel_path / "work"
    host_outputs_dir = host_base / rel_path / "outputs"
    
    # Ensure directories are writable by the container user (65534)
    # We set mode to 777 to allow read/write access for any user in the container
    inputs_dir.chmod(0o777)
    work_dir.chmod(0o777)
    outputs_dir.chmod(0o777)
    
    return (
        inputs_dir,
        work_dir,
        outputs_dir,
        host_inputs_dir,
        host_work_dir,
        host_outputs_dir,
    )


def _read_safe_output_file(path: Path, outputs_root: Path) -> bytes | None:
    """Read a regular file under outputs_root without following symlinks."""
    try:
        if path.parent.resolve() != outputs_root:
            logger.warning("Rejecting output outside root: %s", path.name)
            return None
        st = path.lstat()
    except OSError as exc:
        logger.warning("Failed to stat output file %s: %s", path.name, exc)
        return None
    if stat.S_ISLNK(st.st_mode):
        logger.warning("Rejecting symlink output: %s", path.name)
        return None
    if not stat.S_ISREG(st.st_mode):
        logger.warning("Rejecting non-regular output: %s", path.name)
        return None
    if st.st_size > settings.attachments_max_file_bytes:
        logger.warning(
            "Output file too large %s (%s bytes)", path.name, st.st_size
        )
        return None

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        logger.warning("Failed to open output file %s: %s", path.name, exc)
        return None
    try:
        st_open = os.fstat(fd)
        if not stat.S_ISREG(st_open.st_mode):
            logger.warning("Rejecting non-regular output after open: %s", path.name)
            return None
        if st_open.st_size > settings.attachments_max_file_bytes:
            logger.warning(
                "Output file too large %s (%s bytes)", path.name, st_open.st_size
            )
            return None
        # Re-check path still resolves under root immediately before read (TOCTOU).
        if path.parent.resolve() != outputs_root:
            logger.warning("Rejecting output outside root after open: %s", path.name)
            return None
        chunks: list[bytes] = []
        remaining = st_open.st_size
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        # Trailing bytes after a grow-during-read race: refuse oversized results.
        if len(data) > settings.attachments_max_file_bytes:
            logger.warning(
                "Output file too large after read %s (%s bytes)", path.name, len(data)
            )
            return None
        return data
    except OSError as exc:
        logger.warning("Failed to read output file %s: %s", path.name, exc)
        return None
    finally:
        os.close(fd)


def _collect_outputs(
    outputs_dir: Path, max_files: int | None = None
) -> tuple[list[dict], list[dict]]:
    attachments: list[dict] = []
    output_items: list[dict] = []
    max_files = max_files or settings.attachments_max_files
    total_attachment_bytes = 0
    if not outputs_dir.exists():
        return attachments, output_items
    try:
        outputs_root = outputs_dir.resolve()
    except OSError as exc:
        logger.warning("Failed to resolve outputs dir %s: %s", outputs_dir, exc)
        return attachments, output_items
    for path in sorted(outputs_dir.iterdir()):
        if len(attachments) >= max_files:
            break
        data = _read_safe_output_file(path, outputs_root)
        if data is None:
            continue
        if total_attachment_bytes + len(data) > settings.attachments_max_total_bytes:
            logger.warning(
                "Output files exceed total attachment limit at %s (%s bytes)",
                path.name,
                total_attachment_bytes + len(data),
            )
            continue
        content_type, _ = mimetypes.guess_type(path.name)
        encoded = base64.b64encode(data).decode("ascii")
        attachments.append(
            {
                "file_name": path.name,
                "content_type": content_type or "application/octet-stream",
                "data_base64": encoded,
            }
        )
        total_attachment_bytes += len(data)
        if len(data) <= settings.exec_max_output_file_bytes:
            output_items.append(
                {
                    "file_name": path.name,
                    "content_type": content_type or "application/octet-stream",
                    "data_base64": encoded,
                }
            )
    return attachments, output_items


def _cleanup_stale_containers(client: docker.DockerClient) -> None:
    try:
        stale = client.containers.list(
            all=True, filters={"label": "chatui.exec=true", "status": "exited"}
        )
    except Exception as exc:
        logger.warning("Failed to list stale exec containers: %s", exc)
        return
    for container in stale:
        try:
            container.remove(force=True)
        except Exception:
            continue


def _build_executor_image(client: docker.DockerClient) -> None:
    logger.info("Building executor image: %s", settings.exec_docker_image)
    executor_path = Path("/app/executor")
    if not executor_path.exists():
        # Fallback checks
        if Path("executor").exists():
            executor_path = Path("executor")
        elif Path("backend/executor").exists():
            executor_path = Path("backend/executor")
    
    if not executor_path.exists():
        raise FileNotFoundError(f"Executor build context not found. Expected at {executor_path}")

    try:
        client.images.build(
            path=str(executor_path.absolute()),
            tag=settings.exec_docker_image,
            rm=True,
        )
        logger.info("Executor image built successfully")
    except Exception as exc:
        logger.error("Failed to build executor image: %s", exc)
        raise


def _run_container(
    *,
    host_inputs_dir: Path,
    host_work_dir: Path,
    host_outputs_dir: Path,
    timeout_seconds: int,
) -> tuple[str, str, int | None, bool]:
    client = docker.from_env()
    _cleanup_stale_containers(client)
    labels = {"chatui.exec": "true"}
    container = None
    timed_out = False
    exit_code = None

    def _start_container():
        # memswap_limit == mem_limit disables swap (memory+swap total equals RAM).
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
        volumes = {
            str(host_inputs_dir): {"bind": "/inputs", "mode": "ro"},
            str(host_work_dir): {"bind": "/workspace", "mode": "rw"},
            str(host_outputs_dir): {"bind": "/outputs", "mode": "rw"},
        }
        # docker-py HostConfig has no masked_paths kwarg; inject OCI MaskedPaths
        # so host CPU/RAM/cgroup details are not readable inside the sandbox.
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
        resp = client.api.create_container(
            settings.exec_docker_image,
            command=["python", "/workspace/_sandbox_bootstrap.py"],
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
        return created

    try:
        container = _start_container()
    except ImageNotFound:
        logger.info("Execution image not found, attempting to build...")
        _build_executor_image(client)
        container = _start_container()

    try:
        result = container.wait(timeout=timeout_seconds)
        exit_code = result.get("StatusCode", 1)
    except Exception:
        timed_out = True
        try:
            container.kill()
        except Exception:
            pass
    stdout_logs = (
        container.logs(stdout=True, stderr=False) if container else b""
    )
    stderr_logs = (
        container.logs(stdout=False, stderr=True) if container else b""
    )
    stdout = stdout_logs.decode("utf-8", errors="replace")
    stderr = stderr_logs.decode("utf-8", errors="replace")
    
    if container:
        try:
            container.remove(force=True)
        except (NotFound, APIError):
            pass
    
    try:
        client.close()
    except Exception:
        pass
        
    return stdout, stderr, exit_code, timed_out


async def run_code_execution(
    context: CodeExecutionContext, *, code: str, language: str = "python"
) -> ToolResult:
    if language.lower() not in ALLOWED_LANGUAGES:
        return ToolResult(
            name="code_execution",
            output={"error": f"Unsupported language: {language}"},
        )
    if len(code) > settings.exec_max_code_chars:
        return ToolResult(
            name="code_execution",
            output={"error": "Code exceeds maximum length."},
        )

    chat_uuid = UUID(context.chat_id)
    cowork_docs = list_documents(context.session, chat_uuid)
    try:
        _validate_imports(code, extra_allowed=_cowork_module_names(cowork_docs))
    except ValueError as exc:
        return ToolResult(name="code_execution", output={"error": str(exc)})

    message_ids = context.session.exec(
        select(ChatMessage.id).where(ChatMessage.chat_id == chat_uuid)
    ).all()
    attachments = []
    if message_ids:
        attachments = context.session.exec(
            select(ChatMessageAttachment).where(
                ChatMessageAttachment.message_id.in_(message_ids)
            )
        ).all()

    try:
        (
            inputs_dir,
            work_dir,
            outputs_dir,
            host_inputs_dir,
            host_work_dir,
            host_outputs_dir,
        ) = _prepare_run_dirs(context.chat_id)
    except ValueError as exc:
        return ToolResult(name="code_execution", output={"error": str(exc)})
    inputs = _write_inputs(attachments, inputs_dir)
    if context.agent_id:
        try:
            agent_uuid = UUID(context.agent_id)
        except ValueError:
            agent_uuid = None
        if agent_uuid is not None:
            sources = [
                source
                for source in context.session.exec(
                    select(AgentSource)
                    .where(
                        AgentSource.agent_id == agent_uuid,
                        AgentSource.status == AgentSourceStatus.ready,
                        AgentSource.kind != AgentSourceKind.chat,
                    )
                    .order_by(AgentSource.created_at)
                ).all()
            ]
            used_bytes = sum(
                path.stat().st_size
                for path in inputs_dir.iterdir()
                if path.is_file()
            )
            project_inputs = _write_project_inputs(
                sources,
                inputs_dir,
                used_bytes=used_bytes,
                used_files=len(inputs),
            )
            inputs = inputs + project_inputs

    cowork_files, cowork_path_to_id, cowork_snapshots = _write_cowork_workspace(
        cowork_docs, work_dir
    )

    code_path = work_dir / "main.py"
    code_path.write_text(_auto_display_last_expr(code), encoding="utf-8")
    (work_dir / "_sandbox_bootstrap.py").write_text(_EXEC_BOOTSTRAP, encoding="utf-8")
    def _runner():
        return _run_container(
            host_inputs_dir=host_inputs_dir,
            host_work_dir=host_work_dir,
            host_outputs_dir=host_outputs_dir,
            timeout_seconds=settings.exec_timeout_seconds,
        )
    try:
        stdout, stderr, exit_code, timed_out = await anyio.to_thread.run_sync(_runner)
    except ImageNotFound:
        return ToolResult(
            name="code_execution",
            output={
                "error": (
                    "Execution image not found in the dind Docker daemon. "
                    "Run `docker compose run --rm executor-bootstrap` "
                    "or `docker compose exec dind docker build -t "
                    f"{settings.exec_docker_image} /executor`."
                )
            },
        )
    except APIError as exc:
        return ToolResult(
            name="code_execution",
            output={"error": f"Docker API error: {exc}"},
        )
    except Exception as exc:
        return ToolResult(
            name="code_execution",
            output={"error": f"Execution failed: {exc}"},
        )

    combined = (stdout + stderr).encode("utf-8")
    if len(combined) > settings.exec_max_output_bytes:
        truncated = combined[: settings.exec_max_output_bytes].decode(
            "utf-8", errors="replace"
        )
        stdout = truncated
        stderr = ""

    attachments_out, output_items = _collect_outputs(outputs_dir)
    cowork_updated: list[dict] = []
    if cowork_path_to_id:
        try:
            cowork_updated = _sync_cowork_workspace(
                context.session,
                chat_uuid,
                work_dir,
                path_to_id=cowork_path_to_id,
                snapshots=cowork_snapshots,
            )
        except Exception as exc:
            logger.warning("Failed syncing cowork workspace: %s", exc, exc_info=True)

    return ToolResult(
        name="code_execution",
        output={
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "inputs": inputs,
            "cowork_files": cowork_files,
            "cowork_updated": [
                {
                    "document_id": item.get("document_id"),
                    "file_name": item.get("file_name"),
                    "version": item.get("version"),
                    "synced_path": item.get("synced_path"),
                }
                for item in cowork_updated
            ],
            "outputs": [item["file_name"] for item in attachments_out],
            "output_files": output_items,
            # Full payloads for UI stream (not needed in model-facing summary if huge —
            # kept for agentic_loop coworking events).
            "_cowork_updated_payloads": cowork_updated,
        },
        attachments=attachments_out or None,
    )

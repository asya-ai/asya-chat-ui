import base64
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import UUID, uuid4
import mimetypes

import anyio
import docker
from docker.errors import APIError, ImageNotFound, NotFound
from sqlmodel import Session, select

from app.core.config import settings
from app.models import ChatMessage, ChatMessageAttachment
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

ALLOWED_LANGUAGES = {"python"}
DEFAULT_ALLOWLIST = {
    "numpy",
    "pandas",
    "matplotlib",
    "seaborn",
    "scipy",
    "sklearn",
    "pillow",
    "PIL",
    "requests",
    "bs4",
    "beautifulsoup4",
    "lxml",
    "sympy",
    "openpyxl",
    "docx",
    "pypdf",
}


@dataclass
class CodeExecutionContext:
    session: Session
    org_id: str
    chat_id: str
    network_enabled: bool


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


def _validate_imports(code: str) -> None:
    allowlist = DEFAULT_ALLOWLIST
    stdlib = {name.lower() for name in sys.stdlib_module_names}
    for module in _collect_imports(code):
        base = module.split(".")[0].lower()
        if base in stdlib or base in allowlist:
            continue
        raise ValueError(f"Import not allowed: {module}")


def _sanitize_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "file"


def _write_inputs(
    attachments: Iterable[ChatMessageAttachment], inputs_dir: Path
) -> list[dict]:
    inputs_dir.mkdir(parents=True, exist_ok=True)
    inputs: list[dict] = []
    for attachment in attachments:
        safe_name = _sanitize_filename(attachment.file_name)
        filename = f"{attachment.id}_{safe_name}"
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
        inputs.append(
            {
                "name": attachment.file_name,
                "path": f"/inputs/{filename}",
                "content_type": attachment.content_type,
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
    return (
        inputs_dir,
        work_dir,
        outputs_dir,
        host_inputs_dir,
        host_work_dir,
        host_outputs_dir,
    )


def _collect_outputs(
    outputs_dir: Path, max_files: int = 10
) -> tuple[list[dict], list[dict]]:
    attachments: list[dict] = []
    output_items: list[dict] = []
    if not outputs_dir.exists():
        return attachments, output_items
    for path in sorted(outputs_dir.iterdir()):
        if len(attachments) >= max_files:
            break
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except Exception as exc:
            logger.warning("Failed to read output file %s: %s", path.name, exc)
            continue
        if len(data) > settings.exec_max_output_file_bytes:
            logger.warning(
                "Output file too large %s (%s bytes)", path.name, len(data)
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


def _run_container(
    *,
    host_inputs_dir: Path,
    host_work_dir: Path,
    host_outputs_dir: Path,
    network_enabled: bool,
    timeout_seconds: int,
) -> tuple[str, str, int | None, bool]:
    client = docker.from_env()
    _cleanup_stale_containers(client)
    labels = {"chatui.exec": "true"}
    network_mode = None if network_enabled else "none"
    container = None
    timed_out = False
    exit_code = None
    try:
        container = client.containers.run(
            settings.exec_docker_image,
            command=["python", "/workspace/main.py"],
            detach=True,
            working_dir="/workspace",
            network_mode=network_mode,
            nano_cpus=max(1, int(settings.exec_cpu_limit * 1e9)),
            mem_limit=settings.exec_memory_limit,
            labels=labels,
            volumes={
                str(host_inputs_dir): {"bind": "/inputs", "mode": "ro"},
                str(host_work_dir): {"bind": "/workspace", "mode": "rw"},
                str(host_outputs_dir): {"bind": "/outputs", "mode": "rw"},
            },
        )
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
        return stdout, stderr, exit_code, timed_out
    finally:
        if container:
            try:
                container.remove(force=True)
            except (NotFound, APIError):
                pass
        try:
            client.close()
        except Exception:
            pass


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
    try:
        _validate_imports(code)
    except ValueError as exc:
        return ToolResult(name="code_execution", output={"error": str(exc)})

    chat_uuid = UUID(context.chat_id)
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
    code_path = work_dir / "main.py"
    code_path.write_text(code, encoding="utf-8")
    def _runner():
        return _run_container(
            host_inputs_dir=host_inputs_dir,
            host_work_dir=host_work_dir,
            host_outputs_dir=host_outputs_dir,
            network_enabled=context.network_enabled,
            timeout_seconds=settings.exec_timeout_seconds,
        )
    try:
        stdout, stderr, exit_code, timed_out = await anyio.to_thread.run_sync(_runner)
    except ImageNotFound:
        return ToolResult(
            name="code_execution",
            output={
                "error": (
                    "Execution image not found. Build it with "
                    "`docker compose --profile exec build executor` "
                    "or set EXEC_DOCKER_IMAGE to an available image."
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
    return ToolResult(
        name="code_execution",
        output={
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "inputs": inputs,
            "outputs": [item["file_name"] for item in attachments_out],
            "output_files": output_items,
        },
        attachments=attachments_out or None,
    )

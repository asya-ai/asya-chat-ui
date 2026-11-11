from __future__ import annotations

from dataclasses import dataclass
import base64
import io
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from google import genai
from google.genai import types
import httpx
from openai import BadRequestError, AsyncAzureOpenAI, AsyncOpenAI
from sqlmodel import Session, select
from sqlalchemy import or_

from app.core.config import settings
from app.models import ChatMessage, ChatMessageAttachment, ChatModel, OrgModel, OrgProviderConfig
from app.services.instance_providers import resolve_effective_credentials
from app.services.org_service import require_provider_enabled
from app.services.file_storage import attachment_bytes
from app.services.providers.base import ChatUsage
from app.services.providers.gemini_provider import GeminiProvider
from app.services.providers.openai_provider import _usage_chunk_from_response_usage
from app.services.team_service import allowed_model_ids
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

_IMAGE_USAGE_TOKEN_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "thinking_tokens",
)


def image_usage_token_fields(output: dict | None) -> dict[str, int]:
    """Token fields for UsageEvent / image_usages from an image tool output dict."""
    output = output if isinstance(output, dict) else {}
    return {key: int(output.get(key) or 0) for key in _IMAGE_USAGE_TOKEN_KEYS}


def _apply_usage_fields(output: dict[str, Any], usage: ChatUsage | None) -> None:
    if usage is None:
        return
    output["prompt_tokens"] = int(usage.prompt_tokens or 0)
    output["completion_tokens"] = int(usage.completion_tokens or 0)
    output["total_tokens"] = int(usage.total_tokens or 0)
    output["input_tokens"] = int(usage.input_tokens or 0)
    output["output_tokens"] = int(usage.output_tokens or 0)
    output["cached_tokens"] = int(usage.cached_tokens or 0)
    output["thinking_tokens"] = int(usage.thinking_tokens or 0)


def _usage_from_openai_response(response: Any) -> ChatUsage:
    return _usage_chunk_from_response_usage(getattr(response, "usage", None)).usage


def _usage_from_gemini_response(response: Any) -> ChatUsage:
    return GeminiProvider._usage_from_metadata(getattr(response, "usage_metadata", None))


def _new_generated_image_filename(*, content_type: str = "image/png") -> str:
    """Unique name so multi-image turns rematch correctly after reload."""
    suffix = "png"
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime == "image/jpeg":
        suffix = "jpg"
    elif mime == "image/webp":
        suffix = "webp"
    elif mime == "image/gif":
        suffix = "gif"
    elif "/" in mime:
        maybe = mime.rsplit("/", 1)[-1].strip()
        if maybe and maybe.isalpha() and len(maybe) <= 5:
            suffix = maybe
    return f"generated-{uuid4().hex[:12]}.{suffix}"


@dataclass
class ImageToolContext:
    session: Session
    org_id: str
    chat_id: str | None = None
    user_id: str | None = None
    pending_attachments: list[dict[str, Any]] | None = None


_CATALOG_MAX_ITEMS = 40


def _coerce_attachment_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    raw = value.strip()
    candidates = [raw]
    if "_" in raw:
        candidates.append(raw.split("_", 1)[0])
    for candidate in candidates:
        try:
            return UUID(candidate)
        except ValueError:
            continue
    return None


def _is_generated_image_file_name(file_name: str) -> bool:
    return (file_name or "").startswith("generated-")


def _normalize_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
        return items
    return []


def _attachment_raw_bytes(attachment: ChatMessageAttachment) -> bytes | None:
    raw = attachment_bytes(
        file_path=attachment.file_path,
        data_base64=attachment.data_base64,
    )
    return raw if raw else None


def _attachment_to_base64(attachment: ChatMessageAttachment) -> tuple[str | None, str]:
    raw = _attachment_raw_bytes(attachment)
    if not raw:
        return None, attachment.content_type or "image/png"
    return base64.b64encode(raw).decode("ascii"), attachment.content_type or "image/png"


def _pending_image_attachments(
    pending_attachments: list[dict[str, Any]] | None,
) -> list[ChatMessageAttachment]:
    if not pending_attachments:
        return []
    items: list[ChatMessageAttachment] = []
    for raw in pending_attachments:
        if not isinstance(raw, dict):
            continue
        content_type = str(raw.get("content_type") or "").strip().lower()
        if not content_type.startswith("image/"):
            continue
        file_name = str(raw.get("file_name") or "").strip()
        data_base64 = str(raw.get("data_base64") or "").strip()
        if not (file_name and data_base64):
            continue
        attachment_id = str(raw.get("attachment_id") or "").strip() or str(uuid4())
        raw["attachment_id"] = attachment_id
        try:
            parsed_id = UUID(attachment_id)
        except ValueError:
            attachment_id = str(uuid4())
            raw["attachment_id"] = attachment_id
            parsed_id = UUID(attachment_id)
        items.append(
            ChatMessageAttachment(
                id=parsed_id,
                message_id=UUID(int=0),
                file_name=file_name,
                content_type=content_type,
                data_base64=data_base64,
            )
        )
    return items


def _latest_user_message_id(session: Session, chat_id: str) -> str | None:
    try:
        chat_uuid = UUID(str(chat_id))
    except ValueError:
        return None
    row = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat_uuid)
        .where(ChatMessage.is_current.is_(True))
        .where(ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
    ).first()
    return str(row.id) if row else None


def build_chat_image_catalog(
    session: Session,
    chat_id: str | None,
    *,
    pending_attachments: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Images in the chat for tool resolution (newest last)."""
    if not chat_id:
        return []
    try:
        chat_uuid = UUID(str(chat_id))
    except ValueError:
        return []
    rows = session.exec(
        select(ChatMessageAttachment, ChatMessage.role, ChatMessage.id, ChatMessage.created_at)
        .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
        .where(ChatMessage.chat_id == chat_uuid)
        .where(ChatMessage.is_current.is_(True))
        .where(ChatMessageAttachment.content_type.like("image/%"))
        .order_by(ChatMessage.created_at, ChatMessageAttachment.created_at)
    ).all()
    catalog: list[dict[str, Any]] = []
    for attachment, role, message_id, message_created_at in rows:
        catalog.append(
            {
                "attachment_id": str(attachment.id),
                "file_name": attachment.file_name,
                "message_role": role,
                "message_id": str(message_id),
                "created_at": (
                    message_created_at.isoformat()
                    if isinstance(message_created_at, datetime)
                    else str(message_created_at)
                ),
                "source": "chat",
                "is_generated": _is_generated_image_file_name(attachment.file_name),
            }
        )
    for pending in _pending_image_attachments(pending_attachments):
        catalog.append(
            {
                "attachment_id": str(pending.id),
                "file_name": pending.file_name,
                "message_role": "user",
                "message_id": "pending",
                "created_at": "",
                "source": "pending",
                "is_generated": False,
            }
        )
    return catalog


def format_chat_images_tool_hint(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return " No images in this chat yet."
    shown = catalog[:_CATALOG_MAX_ITEMS]
    parts = [
        f"{item['attachment_id']} · {item['file_name']} · {item.get('message_role', '?')}"
        for item in shown
    ]
    suffix = f" (+{len(catalog) - len(shown)} more)" if len(catalog) > len(shown) else ""
    return " Chat images (attachment_id · file_name · role): " + "; ".join(parts) + suffix


def select_default_edit_base_attachment_id(
    catalog: list[dict[str, Any]],
    *,
    latest_user_message_id: str | None,
) -> tuple[str | None, str | None]:
    if not catalog:
        return None, "No image provided for editing"
    if len(catalog) == 1:
        return str(catalog[0]["attachment_id"]), None

    turn_images = [
        entry
        for entry in catalog
        if entry.get("source") == "pending"
        or (
            latest_user_message_id
            and entry.get("message_id") == latest_user_message_id
        )
    ]
    if len(turn_images) > 1:
        return None, (
            "Multiple images on the latest user message; set image_id to the base canvas."
        )
    if len(turn_images) == 1 and len(catalog) > 1:
        return None, (
            "New image on the latest user message while other chat images exist; "
            "set image_id to the base canvas and reference_image_ids for reference-only uploads."
        )

    generated = [entry for entry in catalog if entry.get("is_generated")]
    if generated:
        return str(generated[-1]["attachment_id"]), None

    return None, "Multiple images in chat; set image_id to the base canvas to edit."


def _resolve_chat_attachment(
    session: Session,
    chat_id: str | None,
    ref: str,
    *,
    pending_attachments: list[dict[str, Any]] | None = None,
) -> ChatMessageAttachment | None:
    if not ref:
        return None
    image_uuid = _coerce_attachment_uuid(ref)
    if image_uuid:
        attachment = session.exec(
            select(ChatMessageAttachment).where(ChatMessageAttachment.id == image_uuid)
        ).first()
        if attachment:
            return attachment
    for pending in _pending_image_attachments(pending_attachments):
        if str(pending.id) == ref or pending.file_name == ref:
            return pending
    if chat_id:
        try:
            chat_uuid = UUID(str(chat_id))
        except ValueError:
            chat_uuid = None
        if chat_uuid is not None:
            return session.exec(
                select(ChatMessageAttachment)
                .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
                .where(ChatMessage.chat_id == chat_uuid)
                .where(ChatMessage.is_current.is_(True))
                .where(ChatMessageAttachment.file_name == ref)
                .order_by(ChatMessage.created_at.desc())
            ).first()
    return None


def _enabled_image_model_ids(
    session: Session, org_id: str, user_id: str | UUID | None = None
) -> list:
    try:
        org_uuid = UUID(str(org_id))
    except ValueError:
        return []
    if user_id is not None:
        try:
            user_uuid = UUID(str(user_id))
        except ValueError:
            return []
        return list(allowed_model_ids(session, org_uuid, user_uuid))
    return list(
        session.exec(
            select(OrgModel.model_id).where(
                OrgModel.org_id == org_uuid, OrgModel.is_enabled.is_(True)
            )
        ).all()
    )


def get_image_model(
    session: Session,
    org_id: str,
    *,
    preferred_provider: str | None = None,
    user_id: str | UUID | None = None,
) -> ChatModel | None:
    enabled_model_ids = _enabled_image_model_ids(session, org_id, user_id)
    if not enabled_model_ids:
        return None
    base_query = select(ChatModel).where(
        ChatModel.id.in_(enabled_model_ids),
        ChatModel.is_active.is_(True),
        or_(
            ChatModel.supports_image_output.is_(True),
            ChatModel.model_name.ilike("%image%"),
        ),
    )
    if preferred_provider:
        preferred = session.exec(
            base_query.where(ChatModel.provider == preferred_provider)
        ).first()
        if preferred:
            return preferred
    model = session.exec(base_query).first()
    return model


def _supports_openai_edit_api(model_name: str) -> bool:
    name = (model_name or "").strip().lower()
    return (
        name == "dall-e-2"
        or name.startswith("gpt-image")
        or name.startswith("chatgpt-image")
    )


def _supports_openai_responses_image_edit(model_name: str) -> bool:
    name = (model_name or "").strip().lower()
    return name.startswith("chatgpt-image")


def _get_edit_compatible_image_model(
    session: Session,
    org_id: str,
    *,
    provider: str,
    exclude_model_id: UUID | None = None,
    user_id: str | UUID | None = None,
) -> ChatModel | None:
    enabled_model_ids = _enabled_image_model_ids(session, org_id, user_id)
    if not enabled_model_ids:
        return None
    candidates = session.exec(
        select(ChatModel).where(
            ChatModel.id.in_(enabled_model_ids),
            ChatModel.is_active.is_(True),
            ChatModel.provider == provider,
            or_(
                ChatModel.supports_image_output.is_(True),
                ChatModel.model_name.ilike("%image%"),
            ),
        )
    ).all()
    for candidate in candidates:
        if exclude_model_id is not None and candidate.id == exclude_model_id:
            continue
        if provider in {"openai", "azure"} and _supports_openai_edit_api(
            candidate.model_name
        ):
            return candidate
    return None


def _get_responses_compatible_image_model(
    session: Session,
    org_id: str,
    *,
    provider: str,
    exclude_model_id: UUID | None = None,
    user_id: str | UUID | None = None,
) -> ChatModel | None:
    if provider != "openai":
        return None
    enabled_model_ids = _enabled_image_model_ids(session, org_id, user_id)
    if not enabled_model_ids:
        return None
    candidates = session.exec(
        select(ChatModel).where(
            ChatModel.id.in_(enabled_model_ids),
            ChatModel.is_active.is_(True),
            ChatModel.provider == provider,
            or_(
                ChatModel.supports_image_output.is_(True),
                ChatModel.model_name.ilike("%image%"),
            ),
        )
    ).all()
    for candidate in candidates:
        if exclude_model_id is not None and candidate.id == exclude_model_id:
            continue
        if _supports_openai_responses_image_edit(candidate.model_name):
            return candidate
    return None


async def generate_image(
    context: ImageToolContext, *, prompt: str, model_override: ChatModel | None = None
) -> ToolResult:
    session = context.session
    model = model_override or get_image_model(
        session,
        context.org_id,
        preferred_provider=model_override.provider if model_override else None,
        user_id=context.user_id,
    )
    if not model:
        logger.info("Image generation requested but no image model for org_id=%s", context.org_id)
        return ToolResult(
            name="generate_image",
            output={"error": "No image model enabled for this organization"},
        )
    logger.info(
        "Generating image with provider=%s model=%s org_id=%s",
        model.provider,
        model.model_name,
        context.org_id,
    )

    if model.provider in {"openai", "azure"}:
        provider_config = require_provider_enabled(session, context.org_id, model.provider)
        creds = resolve_effective_credentials(session, model.provider, provider_config)
        if model.provider == "azure":
            client = AsyncAzureOpenAI(
                api_key=creds.api_key,
                api_version=settings.azure_openai_api_version,
                azure_endpoint=creds.endpoint,
            )
        else:
            client = AsyncOpenAI(
                api_key=creds.api_key,
                base_url=creds.base_url or settings.openai_base_url,
            )
        try:
            request_kwargs: dict[str, Any] = {
                "model": model.model_name,
                "prompt": prompt,
                "size": "1024x1024",
            }
            # gpt-image-* rejects response_format; only older DALL·E accepts it.
            if (model.model_name or "").strip().lower().startswith("dall-e"):
                request_kwargs["response_format"] = "b64_json"
            try:
                result = await client.images.generate(**request_kwargs)
            except BadRequestError as exc:
                if "response_format" in str(exc) and "response_format" in request_kwargs:
                    logger.info(
                        "Image API does not support response_format; retrying without it"
                    )
                    request_kwargs.pop("response_format", None)
                    result = await client.images.generate(**request_kwargs)
                else:
                    raise
            return await _build_image_result(
                "generate_image",
                result,
                model_id=str(model.id),
                image_width=1024,
                image_height=1024,
                image_format="png",
            )
        finally:
            await client.close()

    if model.provider == "gemini":
        provider_config = require_provider_enabled(session, context.org_id, model.provider)
        creds = resolve_effective_credentials(session, model.provider, provider_config)
        client = genai.Client(api_key=creds.api_key)
        response = client.models.generate_content(
            model=model.model_name,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        return _extract_gemini_image(
            "generate_image", response, model_id=str(model.id)
        )

    return ToolResult(
        name="generate_image",
        output={"error": "Image generation not supported for provider"},
    )


async def edit_image(
    context: ImageToolContext,
    *,
    prompt: str,
    image_id: str | None = None,
    image_base64: str | None = None,
    image_content_type: str | None = None,
    reference_image_ids: list[str] | None = None,
    mask_id: str | None = None,
    mask_base64: str | None = None,
    mask_content_type: str | None = None,
    model_override: ChatModel | None = None,
) -> ToolResult:
    session = context.session
    catalog = build_chat_image_catalog(
        session,
        context.chat_id,
        pending_attachments=context.pending_attachments,
    )
    model = model_override or get_image_model(
        session,
        context.org_id,
        preferred_provider=model_override.provider if model_override else None,
        user_id=context.user_id,
    )
    if not model:
        logger.info("Image edit requested but no image model for org_id=%s", context.org_id)
        return ToolResult(
            name="edit_image",
            output={"error": "No image model enabled for this organization"},
        )

    resolved_base_id: str | None = None
    resolved_base_name: str | None = None
    if image_id and not image_base64:
        attachment = _resolve_chat_attachment(
            session,
            context.chat_id,
            image_id,
            pending_attachments=context.pending_attachments,
        )
        if attachment:
            image_base64, image_content_type = _attachment_to_base64(attachment)
            resolved_base_id = str(attachment.id)
            resolved_base_name = attachment.file_name
    if not image_base64 and context.chat_id:
        latest_user_message_id = _latest_user_message_id(session, context.chat_id)
        default_id, ambiguous = select_default_edit_base_attachment_id(
            catalog,
            latest_user_message_id=latest_user_message_id,
        )
        if ambiguous:
            return ToolResult(
                name="edit_image",
                output={
                    "error": ambiguous,
                    "chat_images": catalog,
                },
            )
        if default_id:
            attachment = _resolve_chat_attachment(
                session,
                context.chat_id,
                default_id,
                pending_attachments=context.pending_attachments,
            )
            if attachment:
                image_base64, image_content_type = _attachment_to_base64(attachment)
                resolved_base_id = str(attachment.id)
                resolved_base_name = attachment.file_name
    if mask_id and not mask_base64:
        mask_attachment = _resolve_chat_attachment(
            session,
            context.chat_id,
            mask_id,
            pending_attachments=context.pending_attachments,
        )
        if mask_attachment:
            mask_base64, mask_mime = _attachment_to_base64(mask_attachment)
            mask_content_type = mask_attachment.content_type or mask_mime

    if not image_base64:
        return ToolResult(
            name="edit_image",
            output={
                "error": "No image provided for editing",
                "chat_images": catalog,
            },
        )

    reference_ids = _normalize_id_list(reference_image_ids)
    if resolved_base_id:
        reference_ids = [item for item in reference_ids if item != resolved_base_id]
    reference_payloads: list[tuple[str, str, str]] = []
    for ref in reference_ids:
        ref_attachment = _resolve_chat_attachment(
            session,
            context.chat_id,
            ref,
            pending_attachments=context.pending_attachments,
        )
        if not ref_attachment:
            return ToolResult(
                name="edit_image",
                output={
                    "error": f"Reference image not found: {ref}",
                    "chat_images": catalog,
                },
            )
        ref_b64, ref_type = _attachment_to_base64(ref_attachment)
        if not ref_b64:
            return ToolResult(
                name="edit_image",
                output={"error": f"Reference image empty: {ref_attachment.file_name}"},
            )
        reference_payloads.append((ref_attachment.file_name, ref_type, ref_b64))

    edit_prompt = prompt
    if reference_payloads and model.provider in {"openai", "azure"}:
        ref_names = ", ".join(name for name, _, _ in reference_payloads)
        edit_prompt = (
            f"{prompt}\n\nReference images (likeness/style only, not the edit canvas): {ref_names}"
        ).strip()

    logger.info(
        "Editing image base_id=%s base_file=%s reference_ids=%s provider=%s model=%s org_id=%s",
        resolved_base_id,
        resolved_base_name,
        reference_ids,
        model.provider,
        model.model_name,
        context.org_id,
    )

    supports_edit_api = _supports_openai_edit_api(model.model_name)
    if model.provider in {"openai", "azure"} and not supports_edit_api:
        fallback = _get_edit_compatible_image_model(
            session,
            context.org_id,
            provider=model.provider,
            exclude_model_id=model.id,
            user_id=context.user_id,
        )
        if fallback and fallback.id != model.id:
            logger.info(
                "Image edit model fallback org_id=%s from=%s to=%s",
                context.org_id,
                model.model_name,
                fallback.model_name,
            )
            model = fallback
        else:
            return ToolResult(
                name="edit_image",
                output={
                    "error": (
                        f"Model '{model.model_name}' is not compatible with image edits API. "
                        "Enable an image edit model for this org (chatgpt-image-latest, gpt-image-1.x, or dall-e-2)."
                    )
                },
            )
    supports_edit_api = _supports_openai_edit_api(model.model_name)

    if model.provider in {"openai", "azure"}:
        provider_config = require_provider_enabled(session, context.org_id, model.provider)
        creds = resolve_effective_credentials(session, model.provider, provider_config)
        if model.provider == "azure":
            client = AsyncAzureOpenAI(
                api_key=creds.api_key,
                api_version=settings.azure_openai_api_version,
                azure_endpoint=creds.endpoint,
            )
        else:
            client = AsyncOpenAI(
                api_key=creds.api_key,
                base_url=creds.base_url or settings.openai_base_url,
            )
        try:
            image_bytes = base64.b64decode(image_base64)
            image_file = io.BytesIO(image_bytes)
            image_file.name = "image.png"
            mask_file = None
            if mask_base64:
                mask_bytes = base64.b64decode(mask_base64)
                mask_file = io.BytesIO(mask_bytes)
                mask_file.name = "mask.png"
            request_kwargs: dict[str, Any] = {
                "model": model.model_name,
                "image": image_file,
                "prompt": edit_prompt,
            }
            if (model.model_name or "").strip().lower() == "dall-e-2":
                request_kwargs["response_format"] = "b64_json"
            # OpenAI image edit expects mask to be omitted entirely when unused.
            if mask_file is not None:
                request_kwargs["mask"] = mask_file
            try:
                result = await client.images.edit(**request_kwargs)
            except BadRequestError as exc:
                if "response_format" in str(exc):
                    logger.info(
                        "Image edit API does not support response_format; retrying without it"
                    )
                    request_kwargs.pop("response_format", None)
                    image_file.seek(0)
                    if mask_file is not None:
                        mask_file.seek(0)
                    result = await client.images.edit(**request_kwargs)
                elif "Value must be 'dall-e-2'" in str(exc):
                    fallback = _get_edit_compatible_image_model(
                        session,
                        context.org_id,
                        provider=model.provider,
                        exclude_model_id=model.id,
                        user_id=context.user_id,
                    )
                    if fallback:
                        logger.info(
                            "images.edit fallback org_id=%s from=%s to=%s",
                            context.org_id,
                            model.model_name,
                            fallback.model_name,
                        )
                        request_kwargs["model"] = fallback.model_name
                        image_file.seek(0)
                        if mask_file is not None:
                            mask_file.seek(0)
                        result = await client.images.edit(**request_kwargs)
                        model = fallback
                    else:
                        return ToolResult(
                            name="edit_image",
                            output={
                                "error": (
                                    f"Image edit failed: model '{model.model_name}' is not accepted by image edits API. "
                                    "Use an enabled image edit model."
                                )
                            },
                        )
                else:
                    raise
            tool_result = await _build_image_result(
                "edit_image", result, model_id=str(model.id)
            )
            if isinstance(tool_result.output, dict):
                tool_result.output["base_image_id"] = resolved_base_id
                tool_result.output["base_file_name"] = resolved_base_name
                if reference_ids:
                    tool_result.output["reference_image_ids"] = reference_ids
            return tool_result
        finally:
            await client.close()

    if model.provider == "gemini":
        provider_config = require_provider_enabled(session, context.org_id, model.provider)
        creds = resolve_effective_credentials(session, model.provider, provider_config)
        client = genai.Client(api_key=creds.api_key)
        mime_type = image_content_type or "image/png"
        parts: list[dict[str, Any]] = [{"text": edit_prompt}]
        for ref_name, ref_type, ref_b64 in reference_payloads:
            parts.append({"text": f"Reference image ({ref_name}) — likeness/style only:"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": ref_type,
                        "data": ref_b64,
                    }
                }
            )
        parts.append({"text": "Base image to edit:"})
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": image_base64,
                }
            }
        )
        response = client.models.generate_content(
            model=model.model_name,
            contents=[{"role": "user", "parts": parts}],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        tool_result = _extract_gemini_image("edit_image", response, model_id=str(model.id))
        if isinstance(tool_result.output, dict) and not tool_result.output.get("error"):
            tool_result.output["base_image_id"] = resolved_base_id
            tool_result.output["base_file_name"] = resolved_base_name
            if reference_ids:
                tool_result.output["reference_image_ids"] = reference_ids
        return tool_result

    return ToolResult(
        name="edit_image",
        output={"error": "Image editing not supported for provider"},
    )


async def _build_image_result(
    name: str,
    result: Any,
    *,
    model_id: str | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    image_format: str | None = None,
) -> ToolResult:
    if not result.data:
        return ToolResult(name=name, output={"error": "Image generation failed"})
    image = result.data[0]
    image_base64 = getattr(image, "b64_json", None)
    if not image_base64 and getattr(image, "url", None):
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(image.url)
            response.raise_for_status()
            image_base64 = base64.b64encode(response.content).decode("ascii")
    if not image_base64:
        return ToolResult(name=name, output={"error": "Image generation failed"})
    file_name = _new_generated_image_filename()
    output = {
        "content_type": "image/png",
        "file_name": file_name,
    }
    if model_id:
        output["model_id"] = model_id
    if image_width is not None:
        output["image_width"] = image_width
    if image_height is not None:
        output["image_height"] = image_height
    output["image_count"] = 1
    if image_format:
        output["image_format"] = image_format
    _apply_usage_fields(output, _usage_from_openai_response(result))
    return ToolResult(
        name=name,
        output=output,
        attachments=[
            {
                "file_name": file_name,
                "content_type": "image/png",
                "data_base64": image_base64,
            }
        ],
    )


def _extract_gemini_image(
    name: str, response: Any, *, model_id: str | None = None
) -> ToolResult:
    usage = _usage_from_gemini_response(response)
    candidates = getattr(response, "candidates", []) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                mime_type = getattr(inline_data, "mime_type", "image/png")
                data_base64 = inline_data.data
                if isinstance(data_base64, (bytes, bytearray)):
                    data_base64 = base64.b64encode(data_base64).decode("ascii")
                file_name = _new_generated_image_filename(content_type=mime_type)
                output = {
                    "content_type": mime_type,
                    "file_name": file_name,
                }
                if model_id:
                    output["model_id"] = model_id
                output["image_count"] = 1
                output["image_format"] = mime_type
                _apply_usage_fields(output, usage)
                return ToolResult(
                    name=name,
                    output=output,
                    attachments=[
                        {
                            "file_name": file_name,
                            "content_type": mime_type,
                            "data_base64": data_base64,
                        }
                    ],
                )
    return ToolResult(
        name=name,
        output={"error": "Image generation failed"},
    )


def _extract_openai_response_image(
    name: str,
    response: Any,
    *,
    model_id: str | None = None,
) -> ToolResult:
    output_items = getattr(response, "output", []) or []
    image_base64: str | None = None
    for item in output_items:
        item_type = getattr(item, "type", None)
        if item_type is None and isinstance(item, dict):
            item_type = item.get("type")
        if item_type in {"image_generation_call", "image"}:
            image_base64 = getattr(item, "result", None)
            if image_base64 is None and isinstance(item, dict):
                image_base64 = item.get("result") or item.get("b64_json")
            if image_base64:
                break
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if isinstance(content, list):
            for part in content:
                part_type = getattr(part, "type", None)
                if part_type is None and isinstance(part, dict):
                    part_type = part.get("type")
                if part_type in {"output_image", "image"}:
                    image_base64 = getattr(part, "image_base64", None)
                    if image_base64 is None and isinstance(part, dict):
                        image_base64 = part.get("image_base64") or part.get("b64_json")
                    if image_base64:
                        break
            if image_base64:
                break
    if not image_base64:
        return ToolResult(name=name, output={"error": "Image generation failed"})
    file_name = _new_generated_image_filename()
    output: dict[str, Any] = {
        "content_type": "image/png",
        "file_name": file_name,
        "image_count": 1,
    }
    if model_id:
        output["model_id"] = model_id
    _apply_usage_fields(output, _usage_from_openai_response(response))
    return ToolResult(
        name=name,
        output=output,
        attachments=[
            {
                "file_name": file_name,
                "content_type": "image/png",
                "data_base64": image_base64,
            }
        ],
    )

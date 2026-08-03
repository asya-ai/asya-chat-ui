from __future__ import annotations

from dataclasses import dataclass
import base64
import io
import logging
from typing import Any
from uuid import UUID

from google import genai
from google.genai import types
import httpx
from openai import BadRequestError, AsyncAzureOpenAI, AsyncOpenAI
from sqlmodel import Session, select
from sqlalchemy import or_

from app.core.config import settings
from app.models import ChatMessage, ChatMessageAttachment, ChatModel, OrgModel, OrgProviderConfig
from app.services.org_service import require_provider_enabled
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class ImageToolContext:
    session: Session
    org_id: str
    chat_id: str | None = None


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


def get_image_model(
    session: Session, org_id: str, *, preferred_provider: str | None = None
) -> ChatModel | None:
    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == org_id, OrgModel.is_enabled.is_(True)
        )
    ).all()
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
) -> ChatModel | None:
    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == org_id, OrgModel.is_enabled.is_(True)
        )
    ).all()
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
) -> ChatModel | None:
    if provider != "openai":
        return None
    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == org_id, OrgModel.is_enabled.is_(True)
        )
    ).all()
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
        session, context.org_id, preferred_provider=model_override.provider if model_override else None
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
        if model.provider == "azure":
            client = AsyncAzureOpenAI(
                api_key=provider_config.api_key_override
                if provider_config
                else settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
                azure_endpoint=provider_config.endpoint_override
                if provider_config and provider_config.endpoint_override
                else settings.azure_openai_endpoint,
            )
        else:
            client = AsyncOpenAI(
                api_key=provider_config.api_key_override
                if provider_config
                else settings.openai_api_key,
                base_url=provider_config.base_url_override
                if provider_config
                else settings.openai_base_url,
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
        client = genai.Client(api_key=provider_config.api_key_override if provider_config else settings.gemini_api_key)
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
    mask_id: str | None = None,
    mask_base64: str | None = None,
    mask_content_type: str | None = None,
    model_override: ChatModel | None = None,
) -> ToolResult:
    session = context.session
    model = model_override or get_image_model(
        session, context.org_id, preferred_provider=model_override.provider if model_override else None
    )
    if not model:
        logger.info("Image edit requested but no image model for org_id=%s", context.org_id)
        return ToolResult(
            name="edit_image",
            output={"error": "No image model enabled for this organization"},
        )

    if image_id and not image_base64:
        image_uuid = _coerce_attachment_uuid(image_id)
        attachment = None
        if image_uuid:
            attachment = session.exec(
                select(ChatMessageAttachment).where(ChatMessageAttachment.id == image_uuid)
            ).first()
        if not attachment and context.chat_id:
            # Fallback: caller may pass "<attachment_id>_<filename>" from code-exec style paths.
            attachment = session.exec(
                select(ChatMessageAttachment)
                .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
                .where(ChatMessage.chat_id == context.chat_id)
                .where(ChatMessageAttachment.file_name == image_id)
                .order_by(ChatMessage.created_at.desc())
            ).first()
        if attachment:
            image_base64 = attachment.data_base64
            image_content_type = attachment.content_type
    # If no explicit image was provided, fall back to the latest image attachment in this chat.
    if not image_base64 and context.chat_id:
        try:
            latest_image_attachment = session.exec(
                select(ChatMessageAttachment)
                .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
                .where(ChatMessage.chat_id == context.chat_id)
                .where(ChatMessageAttachment.content_type.like("image/%"))
                .order_by(ChatMessage.created_at.desc())
            ).first()
        except Exception:
            latest_image_attachment = None
        if latest_image_attachment:
            image_base64 = latest_image_attachment.data_base64
            image_content_type = latest_image_attachment.content_type
    if mask_id and not mask_base64:
        mask_uuid = _coerce_attachment_uuid(mask_id)
        mask_attachment = None
        if mask_uuid:
            mask_attachment = session.exec(
                select(ChatMessageAttachment).where(ChatMessageAttachment.id == mask_uuid)
            ).first()
        if mask_attachment:
            mask_base64 = mask_attachment.data_base64
            mask_content_type = mask_attachment.content_type

    if not image_base64:
        return ToolResult(
            name="edit_image",
            output={"error": "No image provided for editing"},
        )

    logger.info(
        "Editing image with provider=%s model=%s org_id=%s",
        model.provider,
        model.model_name,
        context.org_id,
    )

    supports_edit_api = _supports_openai_edit_api(model.model_name)
    if model.provider in {"openai", "azure"} and not supports_edit_api:
        fallback = _get_edit_compatible_image_model(
            session, context.org_id, provider=model.provider, exclude_model_id=model.id
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
        if model.provider == "azure":
            client = AsyncAzureOpenAI(
                api_key=provider_config.api_key_override
                if provider_config
                else settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
                azure_endpoint=provider_config.endpoint_override
                if provider_config and provider_config.endpoint_override
                else settings.azure_openai_endpoint,
            )
        else:
            client = AsyncOpenAI(
                api_key=provider_config.api_key_override
                if provider_config
                else settings.openai_api_key,
                base_url=provider_config.base_url_override
                if provider_config
                else settings.openai_base_url,
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
                "prompt": prompt,
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
            return await _build_image_result(
                "edit_image", result, model_id=str(model.id)
            )
        finally:
            await client.close()

    if model.provider == "gemini":
        provider_config = require_provider_enabled(session, context.org_id, model.provider)
        client = genai.Client(api_key=provider_config.api_key_override if provider_config else settings.gemini_api_key)
        mime_type = image_content_type or "image/png"
        response = client.models.generate_content(
            model=model.model_name,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_base64,
                            }
                        },
                    ],
                }
            ],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        return _extract_gemini_image("edit_image", response, model_id=str(model.id))

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
    output = {
        "content_type": "image/png",
        "file_name": "generated.png",
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
    return ToolResult(
        name=name,
        output=output,
        attachments=[
            {
                "file_name": "generated.png",
                "content_type": "image/png",
                "data_base64": image_base64,
            }
        ],
    )


def _extract_gemini_image(
    name: str, response: Any, *, model_id: str | None = None
) -> ToolResult:
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
                output = {
                    "content_type": mime_type,
                    "file_name": "generated.png",
                }
                if model_id:
                    output["model_id"] = model_id
                output["image_count"] = 1
                output["image_format"] = mime_type
                return ToolResult(
                    name=name,
                    output=output,
                    attachments=[
                        {
                            "file_name": "generated.png",
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
    output: dict[str, Any] = {
        "content_type": "image/png",
        "file_name": "generated.png",
        "image_count": 1,
    }
    if model_id:
        output["model_id"] = model_id
    return ToolResult(
        name=name,
        output=output,
        attachments=[
            {
                "file_name": "generated.png",
                "content_type": "image/png",
                "data_base64": image_base64,
            }
        ],
    )

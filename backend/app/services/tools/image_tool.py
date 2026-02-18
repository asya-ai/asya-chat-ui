from __future__ import annotations

from dataclasses import dataclass
import base64
import io
import logging
from typing import Any

from google import genai
from google.genai import types
import httpx
from openai import BadRequestError, AsyncAzureOpenAI, AsyncOpenAI
from sqlmodel import Session, select
from sqlalchemy import or_

from app.core.config import settings
from app.models import ChatMessageAttachment, ChatModel, OrgModel, OrgProviderConfig
from app.services.org_service import require_provider_enabled
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class ImageToolContext:
    session: Session
    org_id: str


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
            result = await client.images.generate(
                model=model.model_name,
                prompt=prompt,
                size="1024x1024",
                response_format="b64_json",
            )
        except BadRequestError as exc:
            if "response_format" in str(exc):
                logger.info("Image API does not support response_format; retrying without it")
                result = await client.images.generate(
                    model=model.model_name,
                    prompt=prompt,
                    size="1024x1024",
                )
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
        attachment = session.exec(
            select(ChatMessageAttachment).where(ChatMessageAttachment.id == image_id)
        ).first()
        if attachment:
            image_base64 = attachment.data_base64
            image_content_type = attachment.content_type
    if mask_id and not mask_base64:
        mask_attachment = session.exec(
            select(ChatMessageAttachment).where(ChatMessageAttachment.id == mask_id)
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
        image_bytes = base64.b64decode(image_base64)
        image_file = io.BytesIO(image_bytes)
        image_file.name = "image.png"
        mask_file = None
        if mask_base64:
            mask_bytes = base64.b64decode(mask_base64)
            mask_file = io.BytesIO(mask_bytes)
            mask_file.name = "mask.png"
        try:
            result = await client.images.edit(
                model=model.model_name,
                image=image_file,
                mask=mask_file,
                prompt=prompt,
                size="1024x1024",
                response_format="b64_json",
            )
        except BadRequestError as exc:
            if "response_format" in str(exc):
                logger.info("Image edit API does not support response_format; retrying without it")
                result = await client.images.edit(
                    model=model.model_name,
                    image=image_file,
                    mask=mask_file,
                    prompt=prompt,
                    size="1024x1024",
                )
            else:
                raise
        return await _build_image_result("edit_image", result, model_id=str(model.id))

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
        "data_base64": image_base64,
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
                    "data_base64": data_base64,
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

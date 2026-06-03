from __future__ import annotations

import re

from sqlmodel import Session

from app.models import ChatModel

_EMBEDDING_MODEL = re.compile(
    r"(^|[\s/_-])(embedding|embeddings|text-embedding|embed-)([\s/_-]|$)",
    re.IGNORECASE,
)
_NON_CHAT_MODEL = re.compile(
    r"(whisper|tts|transcribe|moderation|davinci|babbage|codex|realtime)",
    re.IGNORECASE,
)
_IMAGE_OUTPUT_MODEL = re.compile(
    r"(dall-e|gpt-image|imagen|stable-diffusion|flux|midjourney)",
    re.IGNORECASE,
)
_IMAGE_INPUT_PROVIDERS = frozenset({"openai", "azure", "gemini", "vertex", "openrouter"})


def _is_non_chat_model(model_name: str) -> bool:
    lowered = model_name.lower()
    if _EMBEDDING_MODEL.search(lowered):
        return True
    if _NON_CHAT_MODEL.search(lowered):
        return True
    return False


def infer_capabilities_for_model(
    provider: str,
    model_name: str,
) -> tuple[bool | None, bool | None]:
    lowered = model_name.lower()

    if _is_non_chat_model(model_name):
        return False, False

    image_output: bool | None = None
    if _IMAGE_OUTPUT_MODEL.search(lowered):
        image_output = True
    elif "image" in lowered and not lowered.startswith("gpt-"):
        image_output = True

    if "vision" in lowered:
        return True, image_output if image_output is not None else False

    if provider == "anthropic":
        return False, image_output

    if provider in {"openai", "azure"}:
        if (
            re.search(r"^gpt-[45]", lowered)
            or re.search(r"^o[0-9]", lowered)
            or lowered.startswith("chatgpt-")
        ):
            return True, image_output if image_output is not None else False
        if lowered.startswith("gpt-4"):
            return True, image_output if image_output is not None else False
        if lowered.startswith("gpt-3.5"):
            return False, image_output

    if provider in {"gemini", "vertex"}:
        if lowered.startswith("gemini-"):
            if "embedding" in lowered:
                return False, False
            return True, image_output if image_output is not None else ("image" in lowered)

    if provider == "openrouter":
        if (
            re.search(r"^gpt-[45]", lowered)
            or lowered.startswith("gpt-4")
            or "gemini" in lowered
        ):
            return True, image_output if image_output is not None else False

    if provider == "groq":
        if "vision" in lowered or "llava" in lowered:
            return True, False

    if provider in _IMAGE_INPUT_PROVIDERS:
        return True, image_output if image_output is not None else False

    if "image" in lowered:
        return True, True

    return None, image_output


def lookup_suggested_capabilities(
    provider: str,
    model_name: str,
) -> tuple[bool | None, bool | None, int | None]:
    from app.services.model_suggestions import get_model_suggestions

    for provider_entry in get_model_suggestions():
        if provider_entry.get("provider") != provider:
            continue
        models = provider_entry.get("models") or []
        if not isinstance(models, list):
            continue
        for item in models:
            if not isinstance(item, dict):
                continue
            if item.get("model_name") != model_name:
                continue
            context_length = item.get("context_length")
            return (
                item.get("supports_image_input"),
                item.get("supports_image_output"),
                int(context_length) if isinstance(context_length, int) else None,
            )
    return None, None, None


def _resolve_without_suggestions(
    provider: str,
    model_name: str,
) -> tuple[bool, bool, int | None]:
    inferred_input, inferred_output = infer_capabilities_for_model(provider, model_name)

    final_input = inferred_input if inferred_input is not None else False
    final_output = inferred_output if inferred_output is not None else False

    return final_input, final_output, None


def resolve_capabilities_for_storage(
    provider: str,
    model_name: str,
    *,
    use_suggestions: bool = False,
) -> tuple[bool, bool, int | None]:
    if not use_suggestions:
        return _resolve_without_suggestions(provider, model_name)

    suggested_input, suggested_output, suggested_context = lookup_suggested_capabilities(
        provider, model_name
    )
    inferred_input, inferred_output = infer_capabilities_for_model(provider, model_name)

    final_input = suggested_input if suggested_input is not None else inferred_input
    final_output = suggested_output if suggested_output is not None else inferred_output

    if final_input is None:
        final_input = False
    if final_output is None:
        final_output = False

    return final_input, final_output, suggested_context


def _apply_missing_capabilities(model: ChatModel) -> bool:
    resolved_input, resolved_output, resolved_context = resolve_capabilities_for_storage(
        model.provider,
        model.model_name,
        use_suggestions=False,
    )
    changed = False
    if model.supports_image_input is None or (
        model.supports_image_input is False and resolved_input is True
    ):
        if model.supports_image_input != resolved_input:
            model.supports_image_input = resolved_input
            changed = True
    if model.supports_image_output is None or (
        model.supports_image_output is False and resolved_output is True
    ):
        if model.supports_image_output != resolved_output:
            model.supports_image_output = resolved_output
            changed = True
    if model.context_length is None and resolved_context is not None:
        model.context_length = resolved_context
        changed = True
    return changed


def ensure_model_capabilities(session: Session, model: ChatModel) -> ChatModel:
    if not _apply_missing_capabilities(model):
        return model
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def ensure_models_capabilities(session: Session, models: list[ChatModel]) -> list[ChatModel]:
    changed_any = False
    for model in models:
        if _apply_missing_capabilities(model):
            session.add(model)
            changed_any = True
    if changed_any:
        session.commit()
        for model in models:
            session.refresh(model)
    return models


def supports_image_input(model: ChatModel) -> bool:
    return model.supports_image_input is True


def supports_image_output(model: ChatModel) -> bool:
    return model.supports_image_output is True

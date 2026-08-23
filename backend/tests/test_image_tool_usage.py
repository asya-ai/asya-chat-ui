from types import SimpleNamespace

import pytest

from app.services.tools.image_tool import (
    _build_image_result,
    _extract_gemini_image,
    _extract_openai_response_image,
    image_usage_token_fields,
)


@pytest.mark.asyncio
async def test_build_image_result_copies_openai_images_usage():
    result = SimpleNamespace(
        data=[SimpleNamespace(b64_json="abc", url=None)],
        usage=SimpleNamespace(
            input_tokens=24,
            output_tokens=1568,
            total_tokens=1592,
            input_tokens_details=SimpleNamespace(image_tokens=0, text_tokens=24),
        ),
    )

    tool_result = await _build_image_result(
        "generate_image",
        result,
        model_id="model-1",
        image_width=1024,
        image_height=1024,
        image_format="png",
    )

    assert tool_result.output["model_id"] == "model-1"
    assert image_usage_token_fields(tool_result.output) == {
        "prompt_tokens": 24,
        "completion_tokens": 1568,
        "total_tokens": 1592,
        "input_tokens": 24,
        "output_tokens": 1568,
        "cached_tokens": 0,
        "thinking_tokens": 0,
    }


def test_extract_gemini_image_copies_usage_metadata():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=40,
            candidates_token_count=1200,
            total_token_count=1240,
            cached_content_token_count=5,
            thoughts_token_count=0,
        ),
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(
                                mime_type="image/png",
                                data="abc",
                            )
                        )
                    ]
                )
            )
        ],
    )

    tool_result = _extract_gemini_image("generate_image", response, model_id="model-2")

    assert image_usage_token_fields(tool_result.output) == {
        "prompt_tokens": 40,
        "completion_tokens": 1200,
        "total_tokens": 1240,
        "input_tokens": 35,
        "output_tokens": 1200,
        "cached_tokens": 5,
        "thinking_tokens": 0,
    }


def test_extract_openai_response_image_copies_usage():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=30,
            output_tokens=900,
            total_tokens=930,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
        output=[
            SimpleNamespace(type="image_generation_call", result="abc123"),
        ],
    )

    tool_result = _extract_openai_response_image(
        "edit_image", response, model_id="model-3"
    )

    assert tool_result.attachments[0]["data_base64"] == "abc123"
    assert image_usage_token_fields(tool_result.output)["total_tokens"] == 930
    assert image_usage_token_fields(tool_result.output)["output_tokens"] == 900

# External OpenAI compatibility (beranekio tester)

In-repo pytest covers our handlers with a fake provider. For **SDK-level** checks against a deployed API, use [openai-compatibility-tester](https://github.com/beranekio/openai-compatibility-tester) (official OpenAI Go SDK).

```bash
chmod +x backend/scripts/run-openai-compat-tester.sh
export OPENAI_BASE_URL=https://your-host/api/v1
export OPENAI_API_KEY=ak_...
export OPENAI_MODEL=gemini-3.8-flash
export OPENAI_EMBEDDING_MODEL=text-embedding-3-large  # if enabled
./backend/scripts/run-openai-compat-tester.sh default
```

## Recommended CI presets

| Preset | When |
|--------|------|
| `default` | Every deploy (models, chat, stream, responses) |
| `chat_completions_vision,chat_completions_tools,chat_completions_multi_turn,completions` | Invoice / integration clients |
| `extended` | Nightly (many suites; expect failures on unimplemented APIs) |

## Baseline: chat.eldigen.com (2026-03-23, pre–models/`created` deploy)

**Default (3/6 pass)**

| Suite | Result |
|-------|--------|
| chat_completions | PASS |
| chat_completions_stream | PASS |
| responses | PASS |
| models | FAIL — missing `created` on list items |
| models_get | FAIL — no `GET /v1/models/{id}` |
| responses_stream | FAIL — 500 (non-OpenAI responses streaming adapter) |

**Integration-focused (4/11 pass)**

| Suite | Result |
|-------|--------|
| chat_completions_vision | PASS |
| chat_completions_tools | PASS |
| chat_completions_multi_turn | PASS |
| completions | PASS |
| chat_completions_tools_stream | FAIL — chunk after terminal `finish_reason` |
| chat_completions_stream_usage | FAIL — usage must be usage-only chunk |
| completions_stream | FAIL — want `text_completion` stream object |
| embeddings / embeddings_batch | FAIL — HTTP 500 |
| chat_completions_json | FAIL — model did not return strict JSON |
| error_responses | FAIL — body `{"detail":...}` not OpenAI `{error:{message,type}}` |

Local repo fixes (pending deploy): `created` + `GET /models/{id}`, stream usage-only chunk, stream terminal handling.

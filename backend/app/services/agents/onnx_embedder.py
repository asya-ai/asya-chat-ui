from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer

# Logical Hugging Face id stays BAAI/bge-m3 (stored on AgentEmbedding rows).
# FastEmbed's TextEmbedding registry does not include this model, so we load
# the community ONNX export instead.
_ONNX_REPOS = {
    "BAAI/bge-m3": "onnx-community/bge-m3-ONNX",
}
_ONNX_ALLOW_PATTERNS = [
    "tokenizer.json",
    "onnx/model.onnx",
    "onnx/model.onnx_data",
    "onnx/model_quantized.onnx",
]
_MAX_LENGTH = 1024


def embedding_cache_slugs(model_name: str) -> list[str]:
    names = [model_name, _ONNX_REPOS.get(model_name, model_name)]
    return [f"models--{name.replace('/', '--')}" for name in names]


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


def _resolve_onnx_path(root: Path) -> Path:
    for name in ("model.onnx", "model_quantized.onnx"):
        for candidate in (root / "onnx" / name, root / name):
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"No ONNX weights found under {root}")


class OnnxTextEmbedder:
    def __init__(self, model_name: str, *, local_files_only: bool = True):
        repo_id = _ONNX_REPOS.get(model_name, model_name)
        cache_dir = snapshot_download(
            repo_id=repo_id,
            allow_patterns=_ONNX_ALLOW_PATTERNS,
            local_files_only=local_files_only,
        )
        root = Path(cache_dir)
        tokenizer_path = root / "tokenizer.json"
        if not tokenizer_path.exists():
            raise FileNotFoundError(f"tokenizer.json missing in {root}")
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            pad_id = 1
        self.tokenizer.enable_padding(direction="right", pad_id=pad_id, pad_token="<pad>")
        self.tokenizer.enable_truncation(max_length=_MAX_LENGTH)
        self.session = ort.InferenceSession(
            str(_resolve_onnx_path(root)),
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        step = max(1, int(batch_size or 16))
        batches = [
            self._embed_batch(texts[index : index + step])
            for index in range(0, len(texts), step)
        ]
        return np.vstack(batches)

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch(texts)
        input_ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [encoding.attention_mask for encoding in encodings], dtype=np.int64
        )
        feeds: dict[str, np.ndarray] = {}
        if "input_ids" in self.input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self.input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self.session.run(None, feeds)[0]
        cls_vectors = np.asarray(hidden[:, 0, :], dtype=np.float32)
        return _l2_normalize(cls_vectors)


def load_onnx_embedder(model_name: str, *, local_files_only: bool = True) -> OnnxTextEmbedder:
    try:
        return OnnxTextEmbedder(model_name, local_files_only=local_files_only)
    except Exception:
        if local_files_only:
            return OnnxTextEmbedder(model_name, local_files_only=False)
        raise


def warmup_onnx_embedder(model_name: str) -> None:
    embedder = load_onnx_embedder(model_name, local_files_only=False)
    embedder.embed(["warmup"])

"""Central settings, read from environment / .env."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5-20251001", "gemini": "gemini-3.8-flash",
                  "nvidia": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"}
_provider = os.getenv("LLM_PROVIDER", "openai").lower()


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai").lower()
    llm_model: str = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(_provider, "gpt-4o-mini")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    reranker_model: str = os.getenv("RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
    qdrant_path: str = os.getenv("QDRANT_PATH", "./data/qdrant")
    collection: str = os.getenv("COLLECTION_NAME", "eng_docs")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "600"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "80"))
    vision_model: str = os.getenv("VISION_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(_provider, "gpt-4o-mini")
    max_images_per_doc: int = int(os.getenv("MAX_IMAGES_PER_DOC", "25"))
    nvidia_base_url: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    nvidia_thinking: bool = os.getenv("NVIDIA_THINKING", "0").lower() in ("1", "true", "yes")
    chats_dir: str = os.getenv("CHATS_DIR", "./data/chats")
    uploads_dir: str = os.getenv("UPLOADS_DIR", "./data/uploads")
    uploads_collection: str = os.getenv("UPLOADS_COLLECTION", "uploads")
    qa_log_path: str = os.getenv("QA_LOG_PATH", "./data/qa_log.jsonl")
    max_upload_mb: float = float(os.getenv("MAX_UPLOAD_MB", "50"))
    upload_ocr: bool = os.getenv("UPLOAD_OCR", "1") not in ("0", "false", "False")
    api_key: str = os.getenv("API_KEY", "")
    sparse_model: str = os.getenv("SPARSE_MODEL", "Qdrant/bm25")
    raw_dir: str = os.getenv("RAW_DIR", "./data/raw")
    processed_dir: str = os.getenv("PROCESSED_DIR", "./data/processed")
    manifest_path: str = os.getenv("MANIFEST_PATH", "./data/manifest.json")


settings = Settings()

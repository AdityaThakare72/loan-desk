"""Settings from environment variables (.env locally, the server's environment on AWS). Fails fast if a key is missing."""
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
POLICY_DIR = ROOT / "policies"
LOG_DIR = Path(os.getenv("LOG_DIR", ROOT / "logs"))

# LLM: any OpenAI-compatible provider. Default: OpenRouter, same key as the course notebooks.
PROVIDERS = {
    "openrouter": ("OPENROUTER_API_KEY", "google/gemini-2.5-flash-lite", "https://openrouter.ai/api/v1"),
    "gemini": ("GOOGLE_API_KEY", "gemini-2.5-flash-lite", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    "openai": ("OPENAI_API_KEY", "gpt-4.1-mini", "https://api.openai.com/v1"),
}
PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").lower()
if PROVIDER not in PROVIDERS:
    raise RuntimeError(f"LLM_PROVIDER must be one of {list(PROVIDERS)}")
KEY_NAME, _default_model, _default_url = PROVIDERS[PROVIDER]
LLM_API_KEY = os.getenv(KEY_NAME, "")
MODEL = os.getenv("LLM_MODEL") or _default_model
BASE_URL = os.getenv("LLM_BASE_URL") or _default_url
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

# Officers who may use the API: "key:Name,key2:Name2"
OFFICERS = {}
for pair in os.getenv("OFFICER_API_KEYS", "").split(","):
    if ":" in pair:
        key, name = pair.split(":", 1)
        OFFICERS[key.strip()] = name.strip()


def check():
    """Call at startup: a service with a missing key should stop at once, not fail on the first request."""
    missing = [n for n, v in [(KEY_NAME, LLM_API_KEY), ("OFFICER_API_KEYS", OFFICERS)] if not v]
    if missing:
        raise RuntimeError(f"Missing settings: {', '.join(missing)}. Add them to .env (see .env.example).")

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
import dotenv

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

# Load .env tự động
dotenv.load_dotenv()

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.cerebras.ai/v1")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL_NAME = os.getenv("LLM_MODEL", "llama3.1-8b")

SEMAPHORE = asyncio.Semaphore(2)
_client: AsyncOpenAI | None = None


def get_client() -> Any | None:
    global _client
    if AsyncOpenAI is None:
        return None
    if _client is None:
        key = os.getenv("LLM_API_KEY", API_KEY)
        if not key:
            return None
        _client = AsyncOpenAI(
            base_url=os.getenv("LLM_BASE_URL", BASE_URL),
            api_key=key,
        )
    return _client


def _clean_json_text(text: str) -> str:
    """Loại bỏ markdown code block nếu mô hình trả về ```json ... ```"""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    return text


async def ask_llm_json(
    prompt: str,
    system_prompt: str,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Gọi LLM và parse kết quả dạng JSON dict một cách an toàn."""
    client = get_client()
    if client is None:
        return {}

    async with SEMAPHORE:
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=os.getenv("LLM_MODEL", MODEL_NAME),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                raw_content = response.choices[0].message.content or "{}"
                cleaned = _clean_json_text(raw_content)
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
                return {}
            except Exception:
                if attempt == max_retries - 1:
                    return {}
                await asyncio.sleep(1.0 * (attempt + 1))
        return {}

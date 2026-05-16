from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass
class ModelMessage:
    role: str
    content: str


class ModelGateway:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream_chat(
        self,
        messages: list[ModelMessage],
        model_name: str | None = None,
        provider: str | None = None,
    ) -> AsyncIterator[str]:
        selected_provider = (provider or self.settings.ai_provider).lower()
        if selected_provider == "nvidia" and self.settings.nvidia_api_key:
            async for token in self._stream_nvidia(messages, model_name or self.settings.ai_model):
                yield token
            return

        async for token in self._stream_fake(messages):
            yield token

    async def _stream_fake(self, messages: list[ModelMessage]) -> AsyncIterator[str]:
        user_text = next((message.content for message in reversed(messages) if message.role == "user"), "")
        response = (
            "I am the demo mini_chatgpt assistant. "
            "I received your message and would answer through the configured model gateway in production. "
            f"Your message was: {user_text}"
        )
        for token in response.split(" "):
            await asyncio.sleep(0.02)
            yield token + " "

    async def _stream_nvidia(self, messages: list[ModelMessage], model_name: str) -> AsyncIterator[str]:
        url = f"{self.settings.nvidia_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.nvidia_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text

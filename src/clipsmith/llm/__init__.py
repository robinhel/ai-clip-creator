"""LLM provider factory."""

from __future__ import annotations

from pathlib import Path

from ..settings import AppConfig, Secrets
from .anthropic_provider import AnthropicProvider
from .base import ClipPick, ClipPicker
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider


def get_provider(
    config: AppConfig, secrets: Secrets, prompt_version: str = "v1", video: Path | None = None
) -> ClipPicker:
    """Return the configured LLM provider."""
    provider = config.llm.provider
    if provider == "anthropic":
        from campaign.budget import budget_from_settings
        from campaign.config import load_settings

        return AnthropicProvider(
            secrets.anthropic_api_key,
            model=config.llm.model_anthropic,
            retry_cfg=config.llm.retry,
            prompt_version=prompt_version,
            budget=budget_from_settings(load_settings()),
            instructions=config.llm.instructions,
            frames=config.llm.frames if video else 0,
            video=video,
            frame_span_s=config.clip.max_seconds,
        )
    if provider == "openai":
        return OpenAIProvider(
            secrets.openai_api_key,
            model=config.llm.model_openai,
            retry_cfg=config.llm.retry,
            prompt_version=prompt_version,
        )
    if provider == "ollama":
        return OllamaProvider(
            model=config.llm.model_ollama,
            retry_cfg=config.llm.retry,
            prompt_version=prompt_version,
        )
    raise ValueError(f"Unknown LLM provider: {provider!r}")


__all__ = [
    "ClipPick",
    "ClipPicker",
    "AnthropicProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "get_provider",
]

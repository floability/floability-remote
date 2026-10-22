"""Provider-neutral model selection for the built-in agent."""

import os
from dataclasses import dataclass
from typing import Any, Optional

from ..application.errors import OperationError


DEFAULT_MODEL = "ollama:qwen3"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
HOSTED_PROVIDERS = {"openai", "anthropic"}


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    name: str
    base_url: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f"{self.provider}:{self.name}"


def parse_model(
    specification: str,
    ollama_base_url: Optional[str] = None,
) -> ModelSelection:
    """Parse hosted provider prefixes without mistaking Ollama tags for providers."""
    value = (specification or DEFAULT_MODEL).strip()
    if not value:
        raise OperationError("invalid_model", "The model name cannot be empty.")

    if value.startswith("ollama/"):
        provider, name = "ollama", value[len("ollama/") :]
    elif value.startswith("ollama:"):
        provider, name = "ollama", value[len("ollama:") :]
    else:
        prefix, separator, remainder = value.partition(":")
        if separator and prefix in HOSTED_PROVIDERS:
            provider, name = prefix, remainder
        else:
            # Bare names and tags such as qwen3:8b are Ollama models.
            provider, name = "ollama", value

    if not name.strip():
        raise OperationError(
            "invalid_model", f"A model name is required after {provider}:."
        )
    base_url = None
    if provider == "ollama":
        base_url = (
            ollama_base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or DEFAULT_OLLAMA_BASE_URL
        )
    return ModelSelection(provider, name.strip(), base_url)


def create_model(selection: ModelSelection) -> Any:
    """Build a PydanticAI model while keeping provider packages optional."""
    if selection.provider == "ollama":
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        return OllamaModel(
            selection.name,
            provider=OllamaProvider(base_url=selection.base_url),
        )
    # PydanticAI resolves these provider-qualified names and their credentials.
    return selection.display_name

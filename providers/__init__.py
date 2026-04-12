"""
providers/__init__.py

Registry of all supported AI providers.

New providers can be added by:
  1. Creating a new module in this folder that subclasses BaseProvider.
  2. Importing it here and adding it to the PROVIDERS dict.

Usage:
    from providers import PROVIDERS, get_provider

    provider = get_provider("Claude", "sk-ant-...")
    explanation = provider.explain("What does this code do?")
"""

from typing import Type

from .anthropic import AnthropicProvider
from .base import BaseProvider
from .google import GoogleProvider
from .groq import GroqProvider
from .mistral import MistralProvider
from .openai import OpenAIProvider

# Order here controls the order providers appear in the GUI dropdown.
PROVIDERS: dict[str, Type[BaseProvider]] = {
    "Claude": AnthropicProvider,
    "ChatGPT": OpenAIProvider,
    "Gemini": GoogleProvider,
    "Mistral": MistralProvider,
    "Groq": GroqProvider,
}


def get_provider(name: str, api_key: str) -> BaseProvider:
    """Instantiate a provider by its display name.

    Args:
        name: Display name of the provider (e.g. "Claude", "ChatGPT").
              Must be a key in PROVIDERS.
        api_key: A valid API key for the chosen provider.

    Returns:
        An initialised BaseProvider subclass ready to call explain().

    Raises:
        ValueError: If `name` is not a recognised provider name.
    """
    if name not in PROVIDERS:
        valid = list(PROVIDERS.keys())
        raise ValueError(
            f"Unknown provider '{name}'. Choose from: {valid}"
        )
    return PROVIDERS[name](api_key)

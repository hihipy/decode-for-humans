"""
anthropic.py

Provider for Anthropic's Claude models.

Dependencies:
    pip install anthropic

API keys:
    https://platform.anthropic.com/account/api-keys
"""

from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    """Calls the Anthropic Messages API to generate plain-English explanations.

    Attributes:
        name: Display name shown in the GUI ("Claude").
        model: Anthropic model identifier used for all requests.
    """

    name: str = "Claude"
    model: str = "claude-opus-4-6"

    def explain(self, prompt: str) -> str:
        """Send a prompt to Claude and return the response text.

        Args:
            prompt: The full prompt string to send to the model.

        Returns:
            The model's plain-text response.

        Raises:
            ImportError: If the `anthropic` package is not installed.
        """
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("Run: pip install anthropic") from exc

        client = anthropic.Anthropic(api_key=self.api_key)

        message = client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        return message.content[0].text

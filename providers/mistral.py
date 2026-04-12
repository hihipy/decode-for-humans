"""
mistral.py

Provider for Mistral AI models.

Dependencies:
    pip install mistralai

API keys:
    https://console.mistral.ai/api-keys
"""

from .base import BaseProvider


class MistralProvider(BaseProvider):
    """Calls the Mistral AI Chat API to generate plain-English explanations.

    Attributes:
        name: Display name shown in the GUI ("Mistral").
        model: Mistral model identifier used for all requests.
    """

    name: str = "Mistral"
    model: str = "mistral-large-latest"

    def explain(self, prompt: str) -> str:
        """Send a prompt to Mistral and return the response text.

        Args:
            prompt: The full prompt string to send to the model.

        Returns:
            The model's plain-text response.

        Raises:
            ImportError: If the `mistralai` package is not installed.
        """
        try:
            from mistralai import Mistral
        except ImportError as exc:
            raise ImportError("Run: pip install mistralai") from exc

        client = Mistral(api_key=self.api_key)

        response = client.chat.complete(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.choices[0].message.content

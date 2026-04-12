"""
openai.py

Provider for OpenAI's ChatGPT models.

Dependencies:
    pip install openai

API keys:
    https://platform.openai.com/api-keys
"""

from .base import BaseProvider


class OpenAIProvider(BaseProvider):
    """Calls the OpenAI Chat Completions API to generate plain-English explanations.

    Attributes:
        name: Display name shown in the GUI ("ChatGPT").
        model: OpenAI model identifier used for all requests.
    """

    name: str = "ChatGPT"
    model: str = "gpt-4o"

    def explain(self, prompt: str) -> str:
        """Send a prompt to ChatGPT and return the response text.

        Args:
            prompt: The full prompt string to send to the model.

        Returns:
            The model's plain-text response.

        Raises:
            ImportError: If the `openai` package is not installed.
        """
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("Run: pip install openai") from exc

        client = OpenAI(api_key=self.api_key)

        response = client.chat.completions.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.choices[0].message.content

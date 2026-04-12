"""
google.py

Provider for Google's Gemini models.

Dependencies:
    pip install google-genai

API keys:
    https://aistudio.google.com/app/apikey
"""

from .base import BaseProvider


class GoogleProvider(BaseProvider):
    """Calls the Google Gemini API to generate plain-English explanations.

    Attributes:
        name: Display name shown in the GUI ("Gemini").
        model: Gemini model identifier used for all requests.
    """

    name: str = "Gemini"
    model: str = "gemini-2.0-flash"

    def explain(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the response text.

        Args:
            prompt: The full prompt string to send to the model.

        Returns:
            The model's plain-text response.

        Raises:
            ImportError: If the `google-genai` package is not installed.
        """
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("Run: pip install google-genai") from exc

        client = genai.Client(api_key=self.api_key)

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
        )

        return response.text

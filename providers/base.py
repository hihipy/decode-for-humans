"""
base.py

Abstract base class that every AI provider must implement.

All providers accept a prompt string and return a plain-text explanation.
Subclasses must override `explain()`; `test_connection()` is provided for
free using a minimal `explain()` call.

Usage:
    Do not instantiate BaseProvider directly. Use a concrete subclass or
    call `get_provider()` from providers/__init__.py.
"""


class BaseProvider:
    """Abstract interface for an AI provider.

    Attributes:
        name: Human-readable display name shown in the GUI (e.g. "Claude").
        model: Model identifier string passed to the provider's API.
        api_key: The user's API key for this provider.
    """

    name: str = "base"
    model: str = ""

    def __init__(self, api_key: str) -> None:
        """Store the API key for use in explain().

        Args:
            api_key: A valid API key for this provider.
        """
        self.api_key = api_key

    def explain(self, prompt: str) -> str:
        """Send a prompt to the AI and return the plain-text response.

        Must be overridden by every concrete provider subclass.

        Args:
            prompt: The full prompt string to send to the model.

        Returns:
            The model's plain-text response.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.
        """
        raise NotImplementedError(
            f"{self.name} provider must implement explain()"
        )

    def test_connection(self) -> bool:
        """Verify the stored API key works by firing a minimal request.

        Returns:
            True if the provider responded successfully, False otherwise.
        """
        try:
            result = self.explain("Say the word OK and nothing else.")
            return bool(result and result.strip())
        except Exception:
            # Intentionally broad — any failure (network, auth, quota)
            # means the connection test did not pass.
            return False

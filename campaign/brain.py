"""Tunt lager runt Claude API som alltid går via budgetspärren."""

from __future__ import annotations

from .budget import Budget
from .config import Settings


class Brain:
    """Ställer en fråga till Claude och får tillbaka ett svar i ett bestämt format."""

    def __init__(self, settings: Settings, budget: Budget, client=None):
        self.settings = settings
        self.budget = budget
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None or bool(self.settings.anthropic_api_key)

    def _get_client(self):
        if self._client is None:
            import anthropic  # importeras först här så att resten fungerar utan paketet

            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    def ask(
        self,
        *,
        purpose: str,
        model: str,
        system: str,
        content,
        schema: dict,
        max_tokens: int = 1024,
    ) -> dict:
        """Skicka text eller bilder till Claude. Svaret följer `schema`.

        Kastar BudgetExceeded före anropet om ett tak är nått.
        """
        self.budget.check()
        response = self._get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            tools=[
                {
                    "name": "svar",
                    "description": "Lämna svaret i exakt det här formatet.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "svar"},
        )
        usage = response.usage
        self.budget.record(
            model,
            purpose,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
        for block in response.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        raise RuntimeError("Claude svarade utan strukturerat svar.")

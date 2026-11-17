"""Domain errors carry a human message *and* a hint written for the LLM.

Small models recover far better from errors that say what to do next ("ask the guest for a
10-digit mobile number") than from stack traces or validation dumps, so every error the
domain raises is phrased as an instruction.
"""

from __future__ import annotations


class DomainError(Exception):
    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return f"{self.message} Hint: {self.hint}" if self.hint else self.message

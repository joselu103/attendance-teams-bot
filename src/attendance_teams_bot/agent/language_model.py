from typing import Protocol


class LanguageModel(Protocol):
    def select_intent(self, message: str) -> str: ...

from dataclasses import dataclass
from typing import Optional, List

SEPARATOR_TOKEN = "<|endoftext|>"


@dataclass(frozen=True)
class Message:
    user: str
    text: Optional[str] = None

    def render(self):
        result = self.user + ":"
        if self.text is not None:
            result += " " + self.text
        return result

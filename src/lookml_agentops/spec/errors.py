from __future__ import annotations


class SpecError(Exception):
    """A problem in an agent spec, with a location and an optional lint rule id."""

    def __init__(self, message: str, *, file: str, line: int = 1, rule_id: str = "LKS001") -> None:
        super().__init__(f"{file}:{line}: {message}")
        self.message = message
        self.file = file
        self.line = line
        self.rule_id = rule_id

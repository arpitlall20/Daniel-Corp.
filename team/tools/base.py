"""A tiny tool abstraction: a name, a description, a JSON schema and a Python function."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Any]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def run(self, **kwargs) -> str:
        try:
            result = self.fn(**kwargs)
            return result if isinstance(result, str) else str(result)
        except Exception as e:  # return errors to the model so it can recover
            return f"ERROR in {self.name}: {type(e).__name__}: {e}"


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


S = {"type": "string"}
I = {"type": "integer"}

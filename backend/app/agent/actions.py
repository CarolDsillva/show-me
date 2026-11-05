"""What the model may return. Code validates all of it before anything is dispatched."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

BROWSER_ACTIONS = ("goto", "click", "fill", "press", "select", "wait_for", "screenshot", "read_text")
REPLAYABLE = ("goto", "click", "fill", "press", "select", "wait_for")


class Expectation(BaseModel):
    """What a correct app would show. If this doesn't hold on replay, the symptom is observed."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["url_contains", "text_visible", "text_not_visible", "selector_visible", "selector_not_visible"]
    value: str = Field(min_length=1, max_length=512)


class NextAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: Literal["goto", "click", "fill", "press", "select", "wait_for", "screenshot", "read_text", "finish"]
    reason: str = Field(default="", max_length=500)
    path: str | None = Field(default=None, max_length=512, pattern=r"^/([^/\\\s][^\\\s]*)?$")
    selector: str | None = Field(default=None, max_length=512)
    value: str | None = Field(default=None, max_length=2000)
    believes_reproduced: bool | None = None
    expectation: Expectation | None = None
    symptom: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _finish_is_complete(self) -> "NextAction":
        if self.action == "finish":
            if self.believes_reproduced is None:
                raise ValueError("finish needs believes_reproduced")
            if self.believes_reproduced and self.expectation is None:
                raise ValueError("finish with believes_reproduced needs an expectation")
        return self

    def for_sandbox(self) -> dict:
        return self.model_dump(include={"action", "path", "selector", "value"}, exclude_none=True)


def parse_next_action(arguments: dict) -> tuple[NextAction | None, str | None]:
    try:
        return NextAction.model_validate(arguments), None
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:5])
        return None, problems[:500]


NEXT_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "next_action",
        "description": "Choose the single next browser action, or finish.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": [*BROWSER_ACTIONS, "finish"]},
                "reason": {"type": "string", "description": "One short sentence."},
                "path": {"type": "string", "description": "For goto: a path starting with /, never a full URL."},
                "selector": {"type": "string", "description": "CSS selector, preferably one listed on the page."},
                "value": {"type": "string", "description": "For fill, press and select."},
                "believes_reproduced": {"type": "boolean", "description": "For finish only."},
                "expectation": {
                    "type": "object",
                    "description": (
                        "For finish with believes_reproduced: what a CORRECT app would show right after your "
                        "last action. It must be false now, because of the bug, and true once the bug is fixed."
                    ),
                    "properties": {
                        "kind": {"type": "string", "enum": [
                            "url_contains", "text_visible", "text_not_visible", "selector_visible", "selector_not_visible",
                        ]},
                        "value": {"type": "string"},
                    },
                    "required": ["kind", "value"],
                },
                "symptom": {"type": "string", "description": "For finish: what goes wrong, in one sentence."},
            },
            "required": ["action"],
        },
    },
}

WRITE_ISSUE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_issue",
        "description": "Write the bug report text.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "A short, specific bug title. At most 80 characters."},
                "summary": {"type": "string", "description": "What is wrong, in at most 3 plain sentences."},
            },
            "required": ["title", "summary"],
        },
    },
}

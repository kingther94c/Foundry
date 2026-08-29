"""The V1 tool surface: nine tools, frozen.

Kept deliberately small. SWE-agent's ablations found concise, guard-railed tools
raise success rates more than additional tools do, and every tool is also a
policy surface to reason about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall
from foundry.core.tools.base import Tool
from foundry.core.tools.command import RunCommand
from foundry.core.tools.files import ListFiles, ReadArtifact, ReadFile, SearchText
from foundry.core.tools.finish import Finish
from foundry.core.tools.git import GitDiff, GitStatus
from foundry.core.tools.patch import ApplyPatch


@dataclass(slots=True)
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        tool = self.tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self.tools))
            raise InvalidToolCall(f"unknown tool {name!r}; available tools: {known}")
        return tool

    def names(self) -> list[str]:
        return sorted(self.tools)

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(self.tools[name].schema() for name in sorted(self.tools))


def default_registry(*, recorder=None) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ListFiles(), SearchText(), ReadFile(), ReadArtifact(),
        ApplyPatch(), RunCommand(recorder=recorder),
        GitStatus(), GitDiff(), Finish(),
    ):
        registry.register(tool)
    return registry

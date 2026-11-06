"""view_image: show the model an image file from the workspace (D-045).

Read-only, and bounded like read_file: the path resolves inside the
workspace, so the model cannot look at a screenshot the user did not put
there. The image rides on the tool result; each adapter sends it the way its
protocol allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from foundry.core import images
from foundry.core.conversation import ToolSchema
from foundry.core.errors import ImageRejected, InvalidToolCall, ToolError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput
from foundry.core.workspace import PathRejected


@dataclass(slots=True)
class ViewImage:
    name: str = "view_image"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Look at an image file in the workspace -- a screenshot, a diagram, a "
                "rendered plot (PNG, JPEG, GIF or WebP, up to "
                f"{images.MAX_IMAGE_BYTES // 1_000_000} MB). The image is attached to "
                "the result for you to see. Example: {\"path\": \"docs/screenshot.png\"}"
            ),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        unknown = set(args) - {"path"}
        if unknown:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(unknown))}")
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise InvalidToolCall("path must be a non-empty string")
        return Operation(tool=self.name, kind=self.kind, args={"path": path},
                         display=f"view {path}", target=path)

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            resolved = ctx.workspace.resolve(op.args["path"])
        except PathRejected as exc:
            raise ToolError(str(exc)) from exc
        try:
            picture = images.load(resolved.absolute, resolved.relative)
        except ImageRejected as exc:
            raise ToolError(f"{resolved.relative} cannot be shown: {exc}") from exc
        return ToolOutput(
            content=f"{resolved.relative} ({picture.describe()}) is attached for you to look at.",
            images=(picture.block,),
            metadata={"media_type": picture.block.media_type, "bytes": picture.size},
        )

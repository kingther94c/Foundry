"""codex's MCP resource tools (D-102): what the servers that offer resources
publish -- documents, pages, records -- listed and read by URI, as
``list_mcp_resources``, ``list_mcp_resource_templates`` and
``read_mcp_resource``, the names codex gives them.

Listing reads a server's own catalogue and is not asked about: nothing the
model writes reaches the server by it -- the server is one of those
configured, and a page's cursor only one the server itself gave. A read names
any URI the model chooses -- nothing confines it to the workspace, as
read_file is confined, and a URI can carry data out -- so it is asked about
like a call to a server's tool, unless a rule allows it
(``tool = "read_mcp_resource"``, ``pattern = "docs *"`` for one server's). What comes back is the server's content and untrusted:
truncated like a tool's, its images attached only for a model that takes them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import ImageRejected, InvalidToolCall, ToolError
from foundry.core.images import MAX_IMAGES_PER_MESSAGE, from_base64 as image_from_base64
from foundry.core.mcp import Client, McpError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle

LIST_RESOURCES = "list_mcp_resources"
LIST_TEMPLATES = "list_mcp_resource_templates"
READ_RESOURCE = "read_mcp_resource"
NAMES = (LIST_RESOURCES, LIST_TEMPLATES, READ_RESOURCE)
MAX_URI_CHARS = 2048
MAX_LISTED = 200          # entries shown from one page of one server's list
MAX_ABOUT_CHARS = 200     # of an entry's description

_ABOUT = {
    LIST_RESOURCES: "List the resources MCP servers offer -- documents, pages, records -- "
                    "by URI, to read with read_mcp_resource.",
    LIST_TEMPLATES: "List the URI templates MCP servers offer: resources read by filling "
                    "in a template's {parameters} and passing the URI to read_mcp_resource.",
    READ_RESOURCE: "Read one resource an MCP server offers, by its URI.",
}
_KEYS = {LIST_RESOURCES: {"server", "cursor"}, LIST_TEMPLATES: {"server", "cursor"},
         READ_RESOURCE: {"server", "uri"}}


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


@dataclass(slots=True)
class ResourceTool:
    """One of the three, over every server that offers resources."""

    name: str
    clients: dict[str, Client]
    # server -> the cursors its pages gave: the only ones sent back to it
    given: dict[str, set[str]] = field(default_factory=dict)

    @property
    def kind(self) -> ToolKind:
        return ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        servers = ", ".join(sorted(self.clients))
        server = {"type": "string", "description": f"the MCP server: one of {servers}"}
        if self.name == READ_RESOURCE:
            properties = {"server": server,
                          "uri": {"type": "string", "description": "the resource's URI"}}
            required = ["server", "uri"]
        else:
            properties = {
                "server": {**server, "description": f"only this server's ({servers}); "
                                                    "every one's first page when left out"},
                "cursor": {"type": "string",
                           "description": "the next page of one server's list, as the "
                                          "previous page gave it"}}
            required = []
        return ToolSchema(name=self.name,
                          description=f"{_ABOUT[self.name]} Servers that offer them: {servers}.",
                          parameters={"type": "object", "properties": properties,
                                      "required": required})

    def validate(self, args: dict[str, Any]) -> Operation:
        if not isinstance(args, dict):
            raise InvalidToolCall(f"{self.name} takes an object of arguments")
        extra = sorted(set(args) - _KEYS[self.name])
        if extra:
            raise InvalidToolCall(f"{self.name} takes no {extra[0]!r}")
        server = args.get("server")
        if server is not None and not isinstance(server, str):
            # `[]` raised a TypeError on the lookup, and ended the session (D-155).
            raise InvalidToolCall(f"server is a server's name: {', '.join(sorted(self.clients))}")
        if server is not None and server not in self.clients:
            raise InvalidToolCall(f"no MCP server {server!r} offers resources; "
                                  f"{', '.join(sorted(self.clients))} do")
        digest_of = json.dumps(args, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(f"{self.name}\x00{digest_of}".encode("utf-8")).hexdigest()[:16]
        if self.name == READ_RESOURCE:
            uri = args.get("uri")
            if server is None:
                raise InvalidToolCall("read_mcp_resource needs the server")
            if not isinstance(uri, str) or not uri.strip():
                raise InvalidToolCall("read_mcp_resource needs the uri, as a list gave it")
            if len(uri) > MAX_URI_CHARS:
                raise InvalidToolCall(f"a uri is at most {MAX_URI_CHARS} characters")
            return Operation(tool=self.name, kind=self.kind, args=args,
                             display=f"{server}: read {uri}",
                             # A rule names the server, then the URI: "docs *".
                             target=f"{server} {uri}", digest=digest)
        cursor = args.get("cursor")
        if cursor is not None:
            if not isinstance(cursor, str):
                raise InvalidToolCall("cursor is the string a previous page gave")
            if server is None:
                raise InvalidToolCall("a cursor continues one server's list: name the server")
            if cursor not in self.given.get(server, ()):
                # Else listing, which is not asked about, would carry what the
                # model wrote to the server.
                raise InvalidToolCall(f"that cursor is not one {server} gave: list its "
                                      "first page, and use the cursor that page names")
        what = "resource templates" if self.name == LIST_TEMPLATES else "resources"
        return Operation(tool=self.name, kind=self.kind, args=args,
                         display=f"{server or 'every MCP server'}: list {what}",
                         target=server or "*", digest=digest)

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        if self.name == READ_RESOURCE:
            text, images = self._read(op.args["server"], op.args["uri"], ctx)
        else:
            text, images = self._list(op.args.get("server"), op.args.get("cursor")), []
        body, truncated = truncate_middle(text, ctx.max_output_bytes)
        artifact_id = ""
        if truncated and ctx.artifacts is not None:
            artifact_id = ctx.artifacts.put_text(text).artifact_id
            body += f"\n\n[full output saved; read_artifact artifact_id=\"{artifact_id}\"]"
        return ToolOutput(content=body, truncated=truncated, artifact_id=artifact_id,
                          metadata={"mcp_server": op.args.get("server") or "*"},
                          images=tuple(images))

    def _list(self, only: str | None, cursor: str | None) -> str:
        templates = self.name == LIST_TEMPLATES
        field = "resourceTemplates" if templates else "resources"
        lines: list[str] = []
        for server in [only] if only else sorted(self.clients):
            try:
                result = self.clients[server].list_resources(cursor, templates=templates)
            except McpError as exc:
                lines.append(f"{server}: could not list them ({exc})")
                continue
            raw = result.get(field)
            entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
            if not entries:
                lines.append(f"{server}: none")
            for entry in entries[:MAX_LISTED]:
                uri = _one_line(entry.get("uriTemplate" if templates else "uri"), MAX_URI_CHARS)
                line = f"{server}: {uri or '(no uri)'}"
                name = _one_line(entry.get("title") or entry.get("name"), MAX_ABOUT_CHARS)
                if name:
                    line += f" -- {name}"
                mime = _one_line(entry.get("mimeType"), 100)
                if mime:
                    line += f" ({mime})"
                about = _one_line(entry.get("description"), MAX_ABOUT_CHARS)
                if about:
                    line += f": {about}"
                lines.append(line)
            if len(entries) > MAX_LISTED:
                lines.append(f"{server}: {len(entries) - MAX_LISTED} more on this page not shown")
            following = result.get("nextCursor")
            if isinstance(following, str) and following:
                self.given.setdefault(server, set()).add(following)
                lines.append(f"{server}: there are more -- {self.name} with server "
                             f"{json.dumps(server)} and cursor {json.dumps(following)}")
        return "\n".join(lines)

    def _read(self, server: str, uri: str, ctx: ToolContext) -> tuple[str, list]:
        try:
            result = self.clients[server].read_resource(uri, cancelled=ctx.cancelled)
        except McpError as exc:
            raise ToolError(f"MCP server {server}: {exc}") from exc
        contents = result.get("contents")
        items = [c for c in contents if isinstance(c, dict)] if isinstance(contents, list) else []
        images: list = []
        parts: list[str] = []
        for item in items:
            where = item.get("uri") if isinstance(item.get("uri"), str) else uri
            mime = item.get("mimeType") if isinstance(item.get("mimeType"), str) else ""
            if len(items) > 1:
                parts.append(f"[{where}{f' ({mime})' if mime else ''}]")
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
                continue
            blob = item.get("blob")
            size = len(blob) * 3 // 4 if isinstance(blob, str) else 0
            if mime.startswith("image/") and ctx.images and len(images) < MAX_IMAGES_PER_MESSAGE:
                try:
                    picture = image_from_base64(blob, f"{server}: {where}")
                except ImageRejected as exc:
                    parts.append(f"[image {where} not shown: {exc}]")
                    continue
                images.append(picture.block)
                parts.append(f"[image {len(images)} attached ({picture.describe()})]")
            else:
                parts.append(f"[binary resource {where} ({mime or 'unknown type'}, about "
                             f"{size} bytes) not shown]")
        return "\n".join(parts) or "(the resource is empty)", images


def register(clients: list[Client], registry, taken: set[str]) -> list[str]:
    """The three tools, over the servers that said they offer resources; the
    names of those servers. None is offered when no server does."""
    offering = {client.server.name: client for client in clients
                if "resources" in client.capabilities}
    if offering:
        for name in NAMES:
            if name not in taken:
                registry.register(ResourceTool(name, offering))
                taken.add(name)
    return sorted(offering)

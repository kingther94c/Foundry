"""Images (D-045): a view_image tool, -i and /image, and an MCP tool's images.

An image is checked before it is sent (type, size, pixels), counted as what a
provider charges for one rather than as its base64, kept once in the session's
artifacts and referenced from the journal by id, rebuilt by a resume, and sent
the way each protocol allows: inside the tool result for Anthropic, as the user
message after the results for the OpenAI-family protocols.
"""

from __future__ import annotations

import base64
import io
import json
import struct
import zlib

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.repl import dispatch, run_task, RunTask
from foundry.core import images
from foundry.core.backends.anthropic import build_body as anthropic_body
from foundry.core.backends.openai_compat import build_body as compat_body
from foundry.core.backends.responses import build_body as responses_body
from foundry.core.config import load_config, parse_settings
from foundry.core.context import ContextManager, MASK_NOTICE, _estimate_tokens
from foundry.core.conversation import (
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    TurnRequest,
)
from foundry.core.errors import ConfigError, ImageRejected, ToolError
from foundry.core.mcp import McpTool, render_result
from foundry.core.redaction import Redactor
from foundry.core.resume import IMAGE_NOT_KEPT, read_journal
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext, ToolKind
from foundry.core.tools.image import ViewImage
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace
from test_mcp import _run, _server, started  # noqa: F401  (fixture)
from test_resume import _core, call, fake_model, repo, turn  # noqa: F401  (fixtures)


def png(width=2, height=3, extra=b"") -> bytes:
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + extra
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def jpeg(width=640, height=480) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + b"\xff" + sof + b"\xff\xd9"   # a fill byte before the SOF


def webp(kind: str, width=300, height=200) -> bytes:
    if kind == "VP8X":
        body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + \
            (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    elif kind == "VP8L":
        bits = (width - 1) | ((height - 1) << 14)
        body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    else:
        body = (b"VP8 " + struct.pack("<I", 10) + b"\x00\x00\x00\x9d\x01\x2a"
                + struct.pack("<HH", width, height))
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def block(data: bytes = b"", name="shot.png") -> ImageBlock:
    return images.from_bytes(data or png(), name).block


# --- checking an image ----------------------------------------------------------------


@pytest.mark.parametrize("data, media_type, size", [
    (png(2, 3), "image/png", (2, 3)),
    (jpeg(640, 480), "image/jpeg", (640, 480)),
    (b"GIF89a" + struct.pack("<HH", 7, 5) + b"\x00" * 10, "image/gif", (7, 5)),
    (webp("VP8X"), "image/webp", (300, 200)),
    (webp("VP8L"), "image/webp", (300, 200)),
    (webp("VP8 "), "image/webp", (300, 200)),
])
def test_the_type_and_size_come_from_the_bytes(data, media_type, size):
    picture = images.from_bytes(data, "x")
    assert picture.block.media_type == media_type
    assert (picture.width, picture.height) == size
    assert base64.b64decode(picture.block.data) == data


def test_what_is_said_about_an_image():
    picture = images.from_bytes(png(2, 3), "shot.png")
    assert picture.describe() == f"PNG, 2x3, {max(1, round(len(png(2, 3)) / 1000))} KB"
    assert images.Picture(picture.block, 1, 1, 2_500_000).describe() == "PNG, 1x1, 2.5 MB"


@pytest.mark.parametrize("data, reason", [
    (b"just some text, not a picture", "not a PNG, JPEG, GIF or WebP"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "header could not be read"),
    # The first chunk is not IHDR, whatever its bytes look like.
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dtEXt" + b"\x00\x00\x01\x00" * 4,
     "header could not be read"),
    (b"\xff\xd8\xff\xd9" + b"\x00" * 20, "header could not be read"),
    # A frame header after the end marker is not this image's.
    (b"\xff\xd8\xff\xd9\x00\x04\x00\x00" + jpeg()[2:], "header could not be read"),
    (png(0, 3), "header could not be read"),
    (png(8001, 1), "8001x1; a side over 8000"),
    (webp("VP8X")[:20], "header could not be read"),
])
def test_an_image_a_provider_would_refuse_is_refused_here(data, reason):
    with pytest.raises(ImageRejected, match=reason):
        images.from_bytes(data, "x")


def test_a_file_too_large_is_refused_before_it_is_read(tmp_path, monkeypatch):
    big = tmp_path / "big.png"
    big.write_bytes(png())
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 10)
    read = []
    monkeypatch.setattr(type(big), "read_bytes", lambda self: read.append(self) or b"")
    with pytest.raises(ImageRejected, match="images over"):
        images.load(big, "big.png")
    assert read == []
    with pytest.raises(ImageRejected, match="no such file"):
        images.load(tmp_path / "missing.png", "missing.png")
    with pytest.raises(ImageRejected, match="images over"):
        images.from_bytes(png(), "x")


def test_base64_from_a_tool_is_checked_too():
    good = base64.b64encode(png()).decode()
    assert images.from_base64(good, "srv: shot").block.data == good
    for data, reason in [(None, "no data"), ("", "no data"), ("@@@@", "not valid base64"),
                         (base64.b64encode(b"text").decode(), "not a PNG")]:
        with pytest.raises(ImageRejected, match=reason):
            images.from_base64(data, "x")
    # Refused from its length, before decoding megabytes it would then refuse.
    with pytest.raises(ImageRejected, match="^it is over"):
        images.from_base64("A" * (images.MAX_IMAGE_BYTES * 2), "x")


# --- the context ------------------------------------------------------------------------


def test_an_image_is_counted_as_what_it_costs_not_as_its_base64():
    huge = ImageBlock("image/png", "A" * 4_000_000)
    assert _estimate_tokens([Message(Role.USER, (huge,))]) == images.IMAGE_TOKEN_ESTIMATE
    result = ToolResultBlock("c1", "x" * 40, images=(huge, huge))
    assert _estimate_tokens([Message(Role.TOOL, (result,))]) == 10 + 2 * images.IMAGE_TOKEN_ESTIMATE


def test_masking_drops_an_old_results_images_however_short_its_text():
    context = ContextManager(system_prompt="t", max_context_tokens=2_000, mask_after_turns=1)
    context.start_turn()
    context.append_tool_result("c1", "ok", images=(block(),))
    for _ in range(3):
        context.start_turn()
        context.append_user("next")
    masked = context.project()[1].blocks[0]
    assert masked.images == () and masked.content == MASK_NOTICE


def test_a_users_images_come_before_their_text_and_text_alone_is_unchanged():
    context = ContextManager(system_prompt="t")
    image = block()
    context.append_user("what is wrong here?", (image,))
    context.append_user("plain")
    assert context.history[0].blocks == (image, TextBlock("what is wrong here?"))
    assert context.history[1] == Message.text(Role.USER, "plain")


def test_a_restored_result_keeps_its_images():
    context = ContextManager(system_prompt="t")
    message = Message(Role.TOOL, (ToolResultBlock("c1", "seen", images=(block(),)),))
    context.restore([message], [0], 0)
    assert context.history[0].blocks[0].images == message.blocks[0].images


# --- on the wire ----------------------------------------------------------------------


def _request(*messages):
    return TurnRequest(messages=(Message.text(Role.SYSTEM, "s"), *messages), tools=(),
                       model="m")


def _conversation():
    image = block(name="docs/a.png")
    assistant = turn(call("c1", "view_image", {"path": "docs/a.png"}),
                     call("c2", "read_file", {"path": "a.py"})).as_message()
    return image, (
        Message(Role.USER, (block(name="mine.png"), TextBlock("look"))),
        assistant,
        Message(Role.TOOL, (ToolResultBlock("c1", "docs/a.png is attached", images=(image,)),)),
        Message(Role.TOOL, (ToolResultBlock("c2", "A = 1"),)),
    )


def test_anthropic_carries_images_in_the_user_message_and_the_tool_result():
    image, messages = _conversation()
    wire = anthropic_body(_request(*messages), stream=True)["messages"]
    user = wire[0]["content"]
    assert user[0]["type"] == "image" and user[0]["source"]["media_type"] == "image/png"
    assert user[1] == {"type": "text", "text": "look"}
    results = wire[2]["content"]
    assert results[0]["content"] == [
        {"type": "text", "text": "docs/a.png is attached"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": image.data}}]
    assert results[1]["content"] == "A = 1"          # no images: the plain string
    empty = Message(Role.TOOL, (ToolResultBlock("c1", "", images=(image,)),))
    only = anthropic_body(_request(messages[0], messages[1], empty), stream=True)
    assert [p["type"] for p in only["messages"][2]["content"][0]["content"]] == ["image"]


def test_chat_completions_sends_a_results_images_as_the_user_message_after_the_results():
    image, messages = _conversation()
    wire = compat_body(_request(*messages, Message.text(Role.USER, "and now?")),
                       stream=False)["messages"]
    assert [m["role"] for m in wire] == ["system", "user", "assistant", "tool", "tool",
                                         "user", "user"]
    assert wire[1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert wire[1]["content"][1] == {"type": "text", "text": "look"}
    assert wire[3]["content"] == "docs/a.png is attached"
    assert wire[5]["content"] == [
        {"type": "text", "text": "The image(s) from tool call c1: docs/a.png"},
        {"type": "image_url", "image_url": {"url": images.data_url(image)}}]
    assert wire[6] == {"role": "user", "content": "and now?"}
    # At the end of the request too, which is where it usually is.
    assert compat_body(_request(*messages), stream=False)["messages"][-1]["role"] == "user"
    plain = compat_body(_request(Message.text(Role.USER, "hi")), stream=False)["messages"]
    assert plain[1] == {"role": "user", "content": "hi"}


def test_responses_sends_them_as_input_images():
    image, messages = _conversation()
    items = responses_body(_request(*messages), stream=False)["input"]
    assert [i["type"] for i in items] == ["message", "function_call", "function_call",
                                          "function_call_output", "function_call_output",
                                          "message"]
    assert items[0]["content"][0] == {"type": "input_image",
                                      "image_url": images.data_url(messages[0].blocks[0])}
    assert items[0]["content"][1] == {"type": "input_text", "text": "look"}
    assert items[5]["role"] == "user" and items[5]["content"][1] == {
        "type": "input_image", "image_url": images.data_url(image)}
    with_more = responses_body(_request(*messages, Message.text(Role.USER, "next")),
                               stream=False)["input"]
    assert with_more[5]["content"][0]["text"].startswith("The image(s) from tool call c1")
    assert with_more[6]["content"] == [{"type": "input_text", "text": "next"}]


# --- view_image -------------------------------------------------------------------------


def _ctx(root):
    return ToolContext(workspace=Workspace(root), artifacts=None, read_tracker=ReadTracker())


def test_view_image_attaches_a_workspace_image(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "shot.png").write_bytes(png(4, 4))
    tool = ViewImage()
    assert tool.kind is ToolKind.READ_ONLY
    output = tool.execute(tool.validate({"path": "docs/shot.png"}), _ctx(tmp_path))
    assert output.content.startswith("docs/shot.png (PNG, 4x4,")
    assert output.images[0].name == "docs/shot.png"
    assert base64.b64decode(output.images[0].data) == png(4, 4)


def test_view_image_stays_in_the_workspace_and_says_what_is_wrong(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (tmp_path / "outside.png").write_bytes(png())
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    tool = ViewImage()
    with pytest.raises(ToolError, match="path escapes the workspace"):
        tool.execute(tool.validate({"path": "../outside.png"}), _ctx(root))
    with pytest.raises(ToolError, match="notes.txt cannot be shown: it is not a PNG"):
        tool.execute(tool.validate({"path": "notes.txt"}), _ctx(root))
    with pytest.raises(ToolError, match="no such file"):
        tool.execute(tool.validate({"path": "gone.png"}), _ctx(root))
    for bad in ({}, {"path": " "}, {"path": "a.png", "zoom": 2}):
        with pytest.raises(Exception, match="path must be|unknown argument"):
            tool.validate(bad)


def test_view_image_is_offered_only_when_the_backend_takes_images():
    assert "view_image" in default_registry().names()
    assert "view_image" not in default_registry(images=False).names()


# --- the runtime and the journal --------------------------------------------------------


def _viewing(tmp_path, *, redactor=None, rounds=2):
    runtime, session, repo = _core(tmp_path, [
        *[turn(call(f"v{n}", "view_image", {"path": "shot.png"})) for n in range(rounds)],
        turn(text="it is a red square")], redactor=redactor)
    (repo / "shot.png").write_bytes(png(5, 5, extra=b""))
    return runtime, session, repo


def _records(session, kind):
    return [r.payload for r in SessionStore.read_records(session.path) if r.type == kind]


def test_a_viewed_image_reaches_the_model_and_is_kept_once_by_id(tmp_path):
    runtime, session, _repo = _viewing(tmp_path, rounds=2)
    runtime.run_turn("what is in shot.png?")
    seen = runtime.backend.seen
    result = seen[1].messages[-1].blocks[0]
    assert base64.b64decode(result.images[0].data) == png(5, 5)
    journal = session.path.read_text(encoding="utf-8")
    assert result.images[0].data not in journal          # by id, never inline
    reply = _records(session, EventType.TOOL_REPLY)[0]
    digest = reply["images"][0]["artifact"]
    assert reply["images"][0] == {"kind": "image", "media_type": "image/png",
                                  "name": "shot.png", "artifact": digest}
    assert (session.path.parent / "artifacts" / digest).read_bytes() == png(5, 5)
    # Two views and three requests, one artifact.
    assert [p.name for p in (session.path.parent / "artifacts").iterdir()] == [digest]
    last = _records(session, EventType.MODEL_REQUEST)[-1]["messages"]
    assert sum(json.dumps(m).count(digest) for m in last) == 2


def test_an_image_the_user_attached_is_journaled_and_sent(tmp_path):
    runtime, session, _repo = _core(tmp_path, [turn(text="a square")])
    image = block(png(3, 3), "mine.png")
    runtime.run_turn("what is this?", (image,))
    first = runtime.backend.seen[0].messages[1]
    assert first.blocks == (image, TextBlock("what is this?"))
    record = _records(session, EventType.USER_INPUT)[0]
    assert record["text"] == "what is this?" and record["images"][0]["name"] == "mine.png"
    # A message without images keeps the record's old shape.
    (tmp_path / "plain").mkdir()
    plain, plain_session, _ = _core(tmp_path / "plain", [turn(text="x")])
    plain.run_turn("hi")
    assert _records(plain_session, EventType.USER_INPUT)[0] == {"text": "hi"}


def test_a_resume_rebuilds_the_images_from_the_artifacts(tmp_path):
    runtime, session, _repo = _viewing(tmp_path, rounds=1)
    runtime.run_turn("look")
    state = read_journal(session.path)
    restored = [b for m in state.history for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert base64.b64decode(restored[0].images[0].data) == png(5, 5)


def test_the_tail_after_the_last_request_is_rebuilt_with_its_images(tmp_path):
    runtime, session, _repo = _core(tmp_path, [])
    image = block(png(2, 2), "mine.png")
    # Recorded, then the session stops before any request carries them.
    runtime._journal(EventType.USER_INPUT, {
        "text": "see", "images": [runtime._block_payload(image, runtime._store_image)]})
    state = read_journal(session.path)
    assert state.history[-1].blocks == (image, TextBlock("see"))


def test_an_image_that_was_not_kept_becomes_a_note_not_a_failed_resume(tmp_path):
    runtime, session, _repo = _viewing(tmp_path, rounds=1)
    runtime.run_turn("look")
    for path in (session.path.parent / "artifacts").iterdir():
        path.unlink()
    state = read_journal(session.path)
    result = [b for m in state.history for b in m.blocks if isinstance(b, ToolResultBlock)][0]
    assert result.images == () and result.content.endswith(IMAGE_NOT_KEPT)
    user = {"kind": "image", "media_type": "image/png", "name": "x", "artifact": None}
    runtime._journal(EventType.USER_INPUT, {"text": "again", "images": [user]})
    assert read_journal(session.path).history[-1].blocks == (TextBlock(IMAGE_NOT_KEPT),
                                                             TextBlock("again"))


def test_an_image_holding_a_known_secret_is_sent_but_not_kept(tmp_path):
    secret = "sk-test-" + "z" * 40
    redactor = Redactor()
    redactor.register(secret)
    runtime, session, repo = _viewing(tmp_path, redactor=redactor, rounds=1)
    # Only the header is read, so the bytes after it need not be a valid chunk.
    (repo / "shot.png").write_bytes(png(5, 5, extra=b"tEXtkey\x00" + secret.encode()))
    runtime.run_turn("look")
    reply = _records(session, EventType.TOOL_REPLY)[0]
    assert reply["images"][0]["artifact"] is None
    assert list((session.path.parent / "artifacts").iterdir()) == []
    assert runtime.backend.seen[1].messages[-1].blocks[0].images     # still shown


# --- MCP ------------------------------------------------------------------------------


def test_an_mcp_tools_images_are_passed_on_when_they_pass_the_checks():
    good = base64.b64encode(png(6, 6)).decode()
    collected: list = []
    text = render_result({"content": [
        {"type": "text", "text": "here"},
        {"type": "image", "data": good, "mimeType": "image/png"},
        {"type": "image", "data": "aGk=", "mimeType": "image/png"},
    ]}, collected, "srv: shot")
    lines = text.splitlines()
    assert lines[0] == "here" and lines[1].startswith("[image 1 attached (PNG, 6x6,")
    assert lines[2] == "[image content not shown: it is not a PNG, JPEG, GIF or WebP image]"
    assert [i.name for i in collected] == ["srv: shot"] and collected[0].data == good
    many = [{"type": "image", "data": good}] * (images.MAX_IMAGES_PER_MESSAGE + 1)
    collected = []
    assert render_result({"content": many}, collected).splitlines()[-1].startswith(
        f"[image content not shown: more than {images.MAX_IMAGES_PER_MESSAGE}")
    assert len(collected) == images.MAX_IMAGES_PER_MESSAGE


class _Client:
    """A server's answer, without a server."""

    def __init__(self, result):
        self.result = result

    def call_tool(self, remote, args, cancelled=None):
        return self.result


def test_an_mcp_tools_image_rides_on_its_result(tmp_path):
    good = base64.b64encode(png(6, 6)).decode()
    tool = McpTool(name="mcp__srv__shot", kind=ToolKind.MUTATOR,
                   client=_Client({"content": [{"type": "image", "data": good}]}),
                   server="srv", remote="shot", description="", parameters={})
    on = tool.execute(tool.validate({}), ToolContext(None, None, None))
    assert [i.data for i in on.images] == [good] and on.images[0].name == "srv: shot"
    off = tool.execute(tool.validate({}), ToolContext(None, None, None, images=False))
    assert off.images == () and "not shown" in off.content


def test_an_mcp_tool_checks_its_images_when_the_backend_takes_them(started, tmp_path):
    _, registry = started(_server())
    output = _run(registry, "mcp__fake__mixed", {}, tmp_path)
    assert output.images == ()
    assert "[image content not shown: it is not a PNG" in output.content


# --- the command line and the REPL ---------------------------------------------------------


def _home(tmp_path, text=""):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return home


def _wiring(repo, tmp_path, *items):
    out = io.StringIO()
    wiring = app.build(repo, home=_home(tmp_path), console=Console(file=out, width=200),
                       overrides={"base_url": "http://127.0.0.1:9/v1", "model": "fake"},
                       settings=parse_settings(list(items)))
    return wiring, out


def test_slash_image_queues_an_image_for_the_next_message(repo, tmp_path, monkeypatch):
    (repo / "shot.png").write_bytes(png())
    monkeypatch.chdir(repo)
    wiring, _ = _wiring(repo, tmp_path)
    out = io.StringIO()
    console = Console(file=out, width=200)
    try:
        dispatch(wiring, console, "image", '"shot.png"')
        assert "attached shot.png (PNG, 2x3" in out.getvalue()
        assert "goes with your next message" in out.getvalue()
        sent = []
        monkeypatch.setattr(type(wiring.runtime), "run_turn",
                            lambda self, text, images=(): sent.append(images) or None)
        run_task(wiring, RunTask("what is it?"))
        run_task(wiring, RunTask("and this?"))
        assert len(sent[0]) == 1 and sent[1] == ()
        dispatch(wiring, console, "image", "")
        assert "usage: /image PATH" in out.getvalue()
        dispatch(wiring, console, "image", "missing.png")
        assert "cannot attach missing.png: there is no such file" in out.getvalue()
        for _ in range(images.MAX_IMAGES_PER_MESSAGE + 1):
            dispatch(wiring, console, "image", "shot.png")
        assert len(wiring.images) == images.MAX_IMAGES_PER_MESSAGE
        assert "at most" in out.getvalue()
    finally:
        app._close(wiring, app.TerminalStatus.CANCELLED, "done")


def test_a_backend_without_images_offers_none(repo, tmp_path, monkeypatch):
    (repo / "shot.png").write_bytes(png())
    monkeypatch.chdir(repo)
    wiring, _ = _wiring(repo, tmp_path, "images=false")
    out = io.StringIO()
    try:
        assert "view_image" not in wiring.runtime.registry.names()
        assert wiring.runtime.tool_ctx.images is False
        dispatch(wiring, Console(file=out, width=200), "image", "shot.png")
        assert "set up without images" in out.getvalue() and wiring.images == []
    finally:
        app._close(wiring, app.TerminalStatus.CANCELLED, "done")
    with pytest.raises(ConfigError, match="backend.images must be true or false"):
        load_config(home=_home(tmp_path, '[backend]\nimages = "yes"\n'))


def test_exec_sends_an_attached_image_and_refuses_a_bad_one(repo, tmp_path, monkeypatch,
                                                            capsys, fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    monkeypatch.setenv("FOUNDRY_HOME", str(_home(
        tmp_path, f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\n'
                  'stream = false\n')))
    shot = tmp_path / "shot.png"
    shot.write_bytes(png(9, 9))
    handler.turns, handler.index = [text_turn("a square"), text_turn("still")], 0
    assert app.main(["exec", "what is this?", "-i", str(shot), "--workspace", str(repo)]) == 10
    capsys.readouterr()
    content = httpd.requests[0]["messages"][1]["content"]
    assert content[0]["image_url"]["url"] == "data:image/png;base64," + \
        base64.b64encode(png(9, 9)).decode()
    assert content[1] == {"type": "text", "text": "what is this?"}
    # The reminder that follows carries no image.
    assert httpd.requests[1]["messages"][-1]["content"].startswith("This run is unattended")

    notes = tmp_path / "notes.txt"
    notes.write_text("hi", encoding="utf-8")
    sent = len(httpd.requests)
    assert app.main(["exec", "what?", "-i", str(notes), "--workspace", str(repo)]) == 2
    assert "is not a PNG" in capsys.readouterr().err
    assert len(httpd.requests) == sent


def test_run_with_a_task_sends_its_image_too(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    monkeypatch.setenv("FOUNDRY_HOME", str(_home(
        tmp_path, f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\n'
                  'stream = false\n')))
    shot = tmp_path / "shot.png"
    shot.write_bytes(png(4, 4))
    handler.turns, handler.index = [text_turn("a square")], 0
    app.main(["run", "what is this?", "--image", str(shot), "--workspace", str(repo)])
    assert "attached" in capsys.readouterr().out
    assert httpd.requests[0]["messages"][1]["content"][0]["type"] == "image_url"
    assert app.main(["run", "what?", "-i", str(tmp_path / "gone.png"),
                     "--workspace", str(repo)]) == 2
    assert len(httpd.requests) == 1

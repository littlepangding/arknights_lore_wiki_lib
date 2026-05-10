"""Mock-based tests for libs/llm_clients.py.

No real LLM calls. subprocess.run and the genai client are monkeypatched.
time.sleep is patched out so the retry loop doesn't slow tests down.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from libs.bases import LLMError, RETRY_LIMIT
from libs import llm_clients
from libs.llm_clients import (
    ClaudeCLIClient,
    DEFAULT_CLAUDE_CLI_PATH,
    DEFAULT_CLAUDE_MODEL,
    GeminiCLIClient,
    GeminiSDKClient,
    make_client,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """All tests use a no-op sleep so retry-loop timings don't matter."""
    monkeypatch.setattr(llm_clients.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _claude_on_path(monkeypatch):
    """Default: shutil.which returns a path so ClaudeCLIClient instantiates.
    Tests that need it to fail override this."""
    monkeypatch.setattr(
        llm_clients.shutil, "which", lambda name: f"/fake/bin/{name}"
    )


def _completed(stdout: str = "ok", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _claude_json(result: str, *, is_error: bool = False) -> str:
    return json.dumps({"result": result, "is_error": is_error})


# ---------- make_client dispatch ----------


def test_make_client_dispatches_by_backend():
    assert isinstance(make_client("cli"), GeminiCLIClient)
    gai = SimpleNamespace(models=SimpleNamespace())
    assert isinstance(make_client("gai", gai_client=gai), GeminiSDKClient)
    assert isinstance(make_client("claude"), ClaudeCLIClient)


def test_make_client_unknown_backend_raises():
    with pytest.raises(ValueError, match="unknown llm backend"):
        make_client("openai")


def test_make_client_passes_kwargs():
    c = make_client("cli", cli_path="/usr/local/bin/gemini", default_model="gemini-3.1-pro")
    assert c.cli_path == "/usr/local/bin/gemini"
    assert c.default_model == "gemini-3.1-pro"


# ---------- GeminiCLIClient ----------


def test_gemini_cli_query_success(monkeypatch):
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return _completed(stdout="response text\n")

    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    out = GeminiCLIClient().query("SYS ", "PROMPT")
    assert out == "response text\n"
    # full prompt is system + prompt concatenated
    argv = captured["argv"]
    assert argv[0] == "gemini"
    assert "-m" in argv and "gemini-3-flash-preview" in argv
    assert "-p" in argv
    full_prompt_idx = argv.index("-p") + 1
    assert argv[full_prompt_idx] == "SYS PROMPT"
    # Read-only mode (no tool execution) — strictly safer than `-y` for batch jobs.
    assert "--approval-mode" in argv
    assert argv[argv.index("--approval-mode") + 1] == "plan"
    assert "-y" not in argv
    assert captured["kw"]["timeout"] == 600


def test_gemini_cli_model_override(monkeypatch):
    seen = {}
    def fake_run(argv, **kw):
        seen["argv"] = argv
        return _completed(stdout="ok")
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    GeminiCLIClient().query("S", "P", model="gemini-3.1-pro")
    assert "gemini-3.1-pro" in seen["argv"]


def test_gemini_cli_nonzero_exit_retries_then_raises(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(1)
        return _completed(stdout="", stderr="boom", returncode=1)
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    with pytest.raises(LLMError, match="exhausted"):
        GeminiCLIClient().query("S", "P")
    assert len(calls) == RETRY_LIMIT


def test_gemini_cli_empty_stdout_retries_then_raises(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(1)
        return _completed(stdout="   \n", returncode=0)
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    with pytest.raises(LLMError, match="exhausted"):
        GeminiCLIClient().query("S", "P")
    assert len(calls) == RETRY_LIMIT


def test_gemini_cli_recovers_after_transient_failure(monkeypatch):
    calls = {"n": 0}
    def fake_run(argv, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return _completed(stdout="", stderr="transient", returncode=1)
        return _completed(stdout="finally ok")
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    out = GeminiCLIClient().query("S", "P")
    assert out == "finally ok"
    assert calls["n"] == 3


# ---------- GeminiSDKClient ----------


def _fake_gai_client(text_or_exc):
    gai = MagicMock()
    if isinstance(text_or_exc, BaseException):
        gai.models.generate_content.side_effect = text_or_exc
    else:
        gai.models.generate_content.return_value = SimpleNamespace(text=text_or_exc)
    return gai


def test_gemini_sdk_query_success():
    gai = _fake_gai_client("from sdk")
    out = GeminiSDKClient(gai_client=gai).query("S ", "P")
    assert out == "from sdk"
    gai.models.generate_content.assert_called_once()
    call_kwargs = gai.models.generate_content.call_args.kwargs
    assert call_kwargs["contents"] == "S P"
    assert call_kwargs["model"] == "gemini-2.5-flash"


def test_gemini_sdk_model_override():
    gai = _fake_gai_client("ok")
    GeminiSDKClient(gai_client=gai).query("S", "P", model="gemini-pro-2026")
    assert gai.models.generate_content.call_args.kwargs["model"] == "gemini-pro-2026"


def test_gemini_sdk_empty_response_retries_then_raises():
    gai = MagicMock()
    gai.models.generate_content.return_value = SimpleNamespace(text="")
    with pytest.raises(LLMError, match="exhausted"):
        GeminiSDKClient(gai_client=gai).query("S", "P")
    assert gai.models.generate_content.call_count == RETRY_LIMIT


def test_gemini_sdk_exception_retries_then_raises():
    gai = MagicMock()
    gai.models.generate_content.side_effect = RuntimeError("api 500")
    with pytest.raises(LLMError, match="exhausted"):
        GeminiSDKClient(gai_client=gai).query("S", "P")
    assert gai.models.generate_content.call_count == RETRY_LIMIT


def test_gemini_sdk_recovers_after_transient_failure():
    gai = MagicMock()
    gai.models.generate_content.side_effect = [
        RuntimeError("transient"),
        SimpleNamespace(text="recovered"),
    ]
    out = GeminiSDKClient(gai_client=gai).query("S", "P")
    assert out == "recovered"


# ---------- ClaudeCLIClient ----------


def test_claude_cli_missing_raises_at_instantiation(monkeypatch):
    monkeypatch.setattr(llm_clients.shutil, "which", lambda name: None)
    with pytest.raises(LLMError, match="claude CLI not found"):
        ClaudeCLIClient()


def test_claude_cli_custom_path_missing_raises(monkeypatch):
    monkeypatch.setattr(llm_clients.shutil, "which", lambda name: None)
    with pytest.raises(LLMError, match="/opt/claude"):
        ClaudeCLIClient(cli_path="/opt/claude")


def test_claude_cli_query_success_argv_shape(monkeypatch):
    captured = {}
    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return _completed(stdout=_claude_json("haiku said hi"))
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)

    out = ClaudeCLIClient().query("SYSTEM", "USER PROMPT")
    assert out == "haiku said hi"

    argv = captured["argv"]
    assert argv[0] == DEFAULT_CLAUDE_CLI_PATH
    assert "--print" in argv
    assert "--bare" not in argv
    assert "--output-format" in argv and "json" in argv
    assert "--no-session-persistence" in argv
    assert "--model" in argv
    model_val = argv[argv.index("--model") + 1]
    assert model_val == DEFAULT_CLAUDE_MODEL
    assert "--system-prompt" in argv
    sys_val = argv[argv.index("--system-prompt") + 1]
    assert sys_val == "SYSTEM"
    assert "USER PROMPT" not in argv
    assert captured["kw"]["input"] == "USER PROMPT"
    assert captured["kw"]["timeout"] == 600
    assert captured["kw"]["text"] is True


def test_claude_cli_model_override(monkeypatch):
    seen = {}
    def fake_run(argv, **kw):
        seen["argv"] = argv
        return _completed(stdout=_claude_json("ok"))
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    ClaudeCLIClient().query("S", "P", model="claude-opus-4-7")
    assert seen["argv"][seen["argv"].index("--model") + 1] == "claude-opus-4-7"


def test_claude_cli_extra_args_threaded(monkeypatch):
    seen = {}
    def fake_run(argv, **kw):
        seen["argv"] = argv
        return _completed(stdout=_claude_json("ok"))
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    ClaudeCLIClient(extra_args=["--max-budget-usd", "0.50"]).query("S", "P")
    assert "--max-budget-usd" in seen["argv"]
    assert "0.50" in seen["argv"]


def test_claude_cli_nonzero_exit_retries_then_raises(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(1)
        return _completed(stdout="", stderr="rate limited", returncode=1)
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    with pytest.raises(LLMError, match="exhausted"):
        ClaudeCLIClient().query("S", "P")
    assert len(calls) == RETRY_LIMIT


def test_claude_cli_recovers_after_transient_failure(monkeypatch):
    calls = {"n": 0}
    def fake_run(argv, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _completed(stdout="", stderr="transient", returncode=1)
        return _completed(stdout=_claude_json("ok now"))
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    out = ClaudeCLIClient().query("S", "P")
    assert out == "ok now"


def test_claude_cli_zero_exit_is_error_retries_then_raises(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(1)
        return _completed(stdout=_claude_json("You've hit your limit", is_error=True))
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    with pytest.raises(LLMError, match="exhausted"):
        ClaudeCLIClient().query("S", "P")
    assert len(calls) == RETRY_LIMIT


def test_claude_cli_invalid_json_retries_then_raises(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(1)
        return _completed(stdout="not json")
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)
    with pytest.raises(LLMError, match="exhausted"):
        ClaudeCLIClient().query("S", "P")
    assert len(calls) == RETRY_LIMIT


# ---------- bases.query_llm delegates correctly ----------


def test_bases_query_llm_delegates_to_cli(monkeypatch):
    """bases.query_llm with backend='cli' should produce the same dispatch
    a direct GeminiCLIClient call would."""
    from libs import bases

    captured = {}
    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _completed(stdout="from cli")
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)

    resp, out = bases.query_llm("cli", "SYS", "PRE ", " POST", "TEXT")
    assert resp is None  # raw-response slot retired
    assert out == "from cli"
    full_prompt = captured["argv"][captured["argv"].index("-p") + 1]
    assert full_prompt == "SYSPRE TEXT POST"


def test_bases_query_llm_delegates_to_gai():
    from libs import bases

    gai = _fake_gai_client("from gai")
    resp, out = bases.query_llm("gai", "S", "PRE", "POST", "T", gai_client=gai)
    assert resp is None
    assert out == "from gai"
    contents = gai.models.generate_content.call_args.kwargs["contents"]
    assert contents == "SPRETPOST"


def test_bases_query_llm_unknown_backend():
    from libs import bases
    with pytest.raises(ValueError, match="unknown llm backend"):
        bases.query_llm("openai", "", "", "", "")


def test_bases_query_llm_rejects_unexpected_kwargs():
    from libs import bases
    with pytest.raises(TypeError, match="unexpected query_llm kwargs"):
        bases.query_llm("cli", "", "", "", "", bogus_arg=1)


def test_bases_query_llm_validated_passes_through(monkeypatch):
    """validated wrapper still works after the delegation refactor."""
    from libs import bases

    monkeypatch.setattr(
        llm_clients.subprocess, "run",
        lambda argv, **kw: _completed(stdout="<a>1</a>\n<b>2</b>"),
    )
    out = bases.query_llm_validated(
        "cli", "S", "PRE ", "", "T", required_tags=["a", "b"]
    )
    assert "<a>1</a>" in out and "<b>2</b>" in out


def test_bases_query_llm_validated_retries_on_missing_tag(monkeypatch):
    from libs import bases

    responses = ["<a>1</a>", "<a>1</a>\n<b>2</b>"]
    def fake_run(argv, **kw):
        return _completed(stdout=responses.pop(0))
    monkeypatch.setattr(llm_clients.subprocess, "run", fake_run)

    out = bases.query_llm_validated(
        "cli", "S", "PRE ", "", "T", required_tags=["a", "b"]
    )
    assert "<b>2</b>" in out


def test_bases_query_llm_validated_works_with_claude_backend(monkeypatch):
    """Regression: legacy generators (get_story_wiki, get_char_wiki_v3)
    used to take a binary cli/else branch that built a Gemini gai_client
    even when llm_backend was 'claude'. The mismatched kwargs would now
    fail dispatch. After build_llm_kwargs, claude must work cleanly
    through the legacy entrypoint."""
    from libs import bases

    monkeypatch.setattr(
        llm_clients.shutil, "which", lambda name: f"/fake/bin/{name}"
    )
    monkeypatch.setattr(
        llm_clients.subprocess, "run",
        lambda argv, **kw: _completed(stdout=_claude_json("<a>1</a>\n<b>2</b>")),
    )
    out = bases.query_llm_validated(
        "claude", "S", "PRE ", "", "T", required_tags=["a", "b"]
    )
    assert "<a>1</a>" in out and "<b>2</b>" in out


def test_bases_query_llm_validated_raises_after_retry(monkeypatch):
    from libs import bases

    monkeypatch.setattr(
        llm_clients.subprocess, "run",
        lambda argv, **kw: _completed(stdout="<a>only a</a>"),
    )
    with pytest.raises(LLMError, match="missing required tags after retry"):
        bases.query_llm_validated(
            "cli", "S", "PRE ", "", "T", required_tags=["a", "b"]
        )


# ---------- build_llm_kwargs (legacy script entrypoint) ----------


def _stub_keys(monkeypatch, mapping):
    """Replace bases.try_get_value with a dict lookup so build_llm_kwargs
    sees a controlled keys.json without touching the real file."""
    from libs import bases
    monkeypatch.setattr(
        bases, "try_get_value", lambda key, default=None: mapping.get(key, default)
    )


def test_build_llm_kwargs_cli_default(monkeypatch):
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {"llm_backend": "cli"})
    backend, kwargs, model = build_llm_kwargs()
    assert backend == "cli"
    assert kwargs == {"model": "gemini-3-flash-preview"}
    assert model == "gemini-3-flash-preview"


def test_build_llm_kwargs_cli_custom_model(monkeypatch):
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {"llm_backend": "cli", "llm_model": "gemini-3-pro"})
    backend, kwargs, model = build_llm_kwargs()
    assert kwargs["model"] == "gemini-3-pro"
    assert model == "gemini-3-pro"


def test_build_llm_kwargs_omits_backend_key(monkeypatch):
    """Bug fix: `backend` must NOT be in the splat-kwargs dict, otherwise
    `query_llm_validated(backend, ..., **kwargs)` raises 'multiple values
    for argument backend'."""
    from libs.bases import build_llm_kwargs

    monkeypatch.setattr(
        llm_clients.shutil, "which", lambda name: f"/fake/bin/{name}"
    )
    for be in ("cli", "claude"):
        _stub_keys(monkeypatch, {"llm_backend": be})
        _, kwargs, _ = build_llm_kwargs()
        assert "backend" not in kwargs

    # Sanity: kwargs are accepted by query_llm_validated without collision.
    _stub_keys(monkeypatch, {"llm_backend": "claude"})
    backend, kwargs, _ = build_llm_kwargs()
    monkeypatch.setattr(
        llm_clients.subprocess, "run",
        lambda argv, **kw: _completed(stdout=_claude_json("<a>1</a>")),
    )
    from libs import bases
    out = bases.query_llm_validated(
        backend, "S", "PRE ", "", "T", required_tags=["a"], **kwargs
    )
    assert "<a>1</a>" in out


def test_build_llm_kwargs_claude_passes_clean_kwargs(monkeypatch):
    """Regression for P1: legacy script entrypoint must NOT mix a
    gai_client into kwargs when backend is claude."""
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {"llm_backend": "claude"})
    backend, kwargs, model = build_llm_kwargs()
    assert backend == "claude"
    assert "gai_client" not in kwargs
    assert model == "claude-haiku-4-5"


def test_build_llm_kwargs_claude_honors_claude_model(monkeypatch):
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {
        "llm_backend": "claude",
        "claude_model": "claude-sonnet-4-6",
    })
    _, kwargs, model = build_llm_kwargs()
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert model == "claude-sonnet-4-6"


def test_build_llm_kwargs_claude_falls_back_to_llm_model(monkeypatch):
    """Regression for P3: when only llm_model is set, claude/gai must
    pick it up rather than dropping to the built-in default."""
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {
        "llm_backend": "claude",
        "llm_model": "shared-override",
    })
    _, _, model = build_llm_kwargs()
    assert model == "shared-override"


def test_build_llm_kwargs_claude_cli_path_threaded(monkeypatch):
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {
        "llm_backend": "claude",
        "claude_cli_path": "/opt/claude/bin/claude",
    })
    _, kwargs, _ = build_llm_kwargs()
    assert kwargs["cli_path"] == "/opt/claude/bin/claude"


def test_build_llm_kwargs_explicit_args_win(monkeypatch):
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {"llm_backend": "claude", "claude_model": "from-keys"})
    _, _, model = build_llm_kwargs(llm_arg="claude", model_arg="from-arg")
    assert model == "from-arg"


def test_build_llm_kwargs_unknown_backend(monkeypatch):
    from libs.bases import build_llm_kwargs
    _stub_keys(monkeypatch, {"llm_backend": "openai"})
    with pytest.raises(ValueError, match="unknown llm backend"):
        build_llm_kwargs()

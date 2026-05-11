"""Unified LLM backend dispatch.

Three backends behind one Protocol — Gemini CLI (default), Gemini SDK,
Claude CLI. Each client owns its own retry loop and surfaces LLMError on
persistent failure. `libs/kb/summarize.py` is the first consumer.

`bases.query_llm` and `query_llm_validated` delegate here so existing
generator scripts (`get_story_wiki`, `get_char_wiki_v3`) don't need to change.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from libs.bases import (
    DEFAULT_CLI_MODEL,
    DEFAULT_GAI_MODEL,
    LLMError,
    LLMTerminalError,
    RETRY_LIMIT,
    RETRY_SLEEP_TIME,
    archive_llm_output,
    repair_tag_format,
)

_TERMINAL_PATTERNS = (
    "may not exist or you may not have access to it",  # claude/gemini wrong model
    "rate limit",
    "RATE_LIMIT",
    "RESOURCE_EXHAUSTED",
    "quota",
    "429",
    "PERMISSION_DENIED",
    "invalid api key",
    "UNAUTHENTICATED",
)


def _is_terminal_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(p in msg for p in _TERMINAL_PATTERNS)

DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5"
DEFAULT_CLAUDE_CLI_PATH = "claude"


class LLMClient(Protocol):
    default_model: str

    def query(self, system: str, prompt: str, *, model: Optional[str] = None) -> str: ...


def _retry(call, *, label: str) -> str:
    last_exc: Optional[BaseException] = None
    for attempt in range(RETRY_LIMIT):
        try:
            return call()
        except Exception as e:
            if _is_terminal_error(e):
                raise LLMTerminalError(f"{label} terminal error: {e}") from e
            last_exc = e
            print(f"{label} query failed: {e}")
            time.sleep(RETRY_SLEEP_TIME * (attempt + 1))
    raise LLMError(f"{label} exhausted {RETRY_LIMIT} retries: {last_exc}")


def _run_cli(label: str, argv: list[str]) -> str:
    """Common CLI subprocess body shared by GeminiCLIClient and ClaudeCLIClient.
    Wraps the run + returncode-check + empty-stdout-check in `_retry`."""
    def _call() -> str:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{label} exit {proc.returncode}: "
                f"stderr={proc.stderr.strip()[:300]!r} "
                f"stdout={proc.stdout.strip()[:300]!r}"
            )
        if not proc.stdout.strip():
            raise RuntimeError(f"{label} returned empty output")
        return proc.stdout

    return _retry(_call, label=label)


@dataclass
class GeminiCLIClient:
    cli_path: str = "gemini"
    default_model: str = DEFAULT_CLI_MODEL

    def query(self, system: str, prompt: str, *, model: Optional[str] = None) -> str:
        # `--approval-mode plan` is read-only: the CLI refuses to call any
        # tool. Strictly safer than `-y` (YOLO auto-approve) for batch
        # text-in / text-out summarization, and silences the YOLO banner.
        return _run_cli("gemini cli", [
            self.cli_path,
            "-m", model or self.default_model,
            "-p", system + prompt,
            "--approval-mode", "plan",
            "-o", "text",
        ])


@dataclass
class GeminiSDKClient:
    gai_client: object  # google.genai.Client
    default_model: str = DEFAULT_GAI_MODEL

    def query(self, system: str, prompt: str, *, model: Optional[str] = None) -> str:
        contents = system + prompt
        m = model or self.default_model

        def _call() -> str:
            response = self.gai_client.models.generate_content(
                model=m, contents=contents
            )
            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise RuntimeError("gai backend returned empty output")
            return text

        return _retry(_call, label="gemini sdk")


@dataclass
class ClaudeCLIClient:
    cli_path: str = DEFAULT_CLAUDE_CLI_PATH
    default_model: str = DEFAULT_CLAUDE_MODEL
    extra_args: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if shutil.which(self.cli_path) is None:
            raise LLMError(
                f"claude CLI not found on $PATH (looked for {self.cli_path!r}). "
                "Install Claude Code or set claude_cli_path in keys.json."
            )

    def query(self, system: str, prompt: str, *, model: Optional[str] = None) -> str:
        # --bare would force ANTHROPIC_API_KEY-only auth and skip OAuth/keychain,
        # which fails when the user is logged in via subscription. Run without it
        # so OAuth credentials are honored; CLAUDE.md auto-discovery is acceptable
        # noise since the summary output is tag-validated.
        argv = [
            self.cli_path,
            "--print",
            "--model", model or self.default_model,
            "--system-prompt", system,
            "--output-format", "json",
            "--no-session-persistence",
            *self.extra_args,
        ]

        def _call() -> str:
            proc = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"claude cli exit {proc.returncode}: "
                    f"stderr={proc.stderr.strip()[:300]!r} "
                    f"stdout={proc.stdout.strip()[:300]!r}"
                )
            if not proc.stdout.strip():
                raise RuntimeError("claude cli returned empty output")
            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    "claude cli returned invalid json: "
                    f"{proc.stdout.strip()[:300]!r}"
                ) from e
            if payload.get("is_error"):
                result = payload.get("result", "Unknown Claude CLI error")
                raise RuntimeError(f"claude cli reported error: {result!r}")
            result = payload.get("result")
            if not isinstance(result, str) or not result.strip():
                raise RuntimeError("claude cli returned empty result")
            return result

        return _retry(_call, label="claude cli")


def query_with_validated_tags(
    client: LLMClient,
    system: str,
    prompt: str,
    required_tags: list[str],
    *,
    model: Optional[str] = None,
    archive_label: Optional[str] = None,
) -> str:
    """Query `client`, repairing malformed tag output (`bases.repair_tag_format`)
    before falling back to one re-ask-with-reminder, then raising `LLMError`.

    The `raw head: …` logging is here because the offending response is
    otherwise thrown away — these failures are a pain to debug after the fact.
    `archive_label`, if set and archiving is enabled (`bases.set_llm_archive_dir`),
    stashes every raw response under that label (they cost tokens; the kept
    summary is only a canonicalized subset).
    """
    out = client.query(system, prompt, model=model)
    if archive_label:
        archive_llm_output(archive_label, out, kind="try1")
    repaired, missing = repair_tag_format(out, required_tags)
    if not missing:
        return repaired
    print(
        f"LLM output still missing tags {missing} after lenient repair; "
        f"raw head: {out[:300]!r} — retrying once with explicit reminder"
    )
    reminder = (
        f"\n注意：上一次输出缺少必须的标签 {missing}。"
        f"请确保输出严格包含所有需要的标签，每个都用 <标签名>...</标签名> 完整包裹（包括闭合标签）：{required_tags}。\n"
    )
    out2 = client.query(system, prompt + reminder, model=model)
    if archive_label:
        archive_llm_output(archive_label, out2, kind="try2")
    repaired2, still = repair_tag_format(out2, required_tags)
    if still:
        raise LLMError(
            f"output missing required tags after retry: {still}; "
            f"raw head: {out2[:300]!r}"
        )
    return repaired2


def make_client(backend: str = "cli", **kwargs) -> LLMClient:
    """Factory.

    backend:
      "cli"    → GeminiCLIClient   (kwargs: cli_path, default_model)
      "gai"    → GeminiSDKClient   (kwargs: gai_client, default_model)
      "claude" → ClaudeCLIClient   (kwargs: cli_path, default_model, extra_args)
    """
    if backend == "cli":
        return GeminiCLIClient(**kwargs)
    if backend == "gai":
        return GeminiSDKClient(**kwargs)
    if backend == "claude":
        return ClaudeCLIClient(**kwargs)
    raise ValueError(f"unknown llm backend: {backend!r}")

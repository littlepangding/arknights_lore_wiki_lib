"""Unified LLM backend dispatch.

Three backends behind one Protocol — Gemini CLI (default), Gemini SDK,
Claude CLI. Each client owns its own retry loop and surfaces LLMError on
persistent failure. `libs/kb/summarize.py` is the first consumer.

`bases.query_llm` and `query_llm_validated` delegate here so existing
generator scripts (`get_story_wiki`, `get_char_wiki_v3`) don't need to change.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from libs.bases import (
    DEFAULT_CLI_MODEL,
    DEFAULT_GAI_MODEL,
    LLMError,
    RETRY_LIMIT,
    RETRY_SLEEP_TIME,
    extract_tagged_contents,
)

DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5"
DEFAULT_CLAUDE_CLI_PATH = "claude"


class LLMClient(Protocol):
    def query(self, system: str, prompt: str, *, model: Optional[str] = None) -> str: ...


def _retry(call, *, label: str) -> str:
    last_exc: Optional[BaseException] = None
    for it in range(RETRY_LIMIT):
        try:
            return call()
        except Exception as e:
            last_exc = e
            print(f"{label} query failed: {e}")
            time.sleep(RETRY_SLEEP_TIME * (it + 1))
    raise LLMError(f"{label} exhausted {RETRY_LIMIT} retries: {last_exc}")


@dataclass
class GeminiCLIClient:
    cli_path: str = "gemini"
    default_model: str = DEFAULT_CLI_MODEL

    def query(self, system: str, prompt: str, *, model: Optional[str] = None) -> str:
        full_prompt = system + prompt
        m = model or self.default_model
        # `--approval-mode plan` is read-only: the CLI refuses to call any
        # tool. We want pure text-in / text-out for summarization, so
        # disabling tool execution outright is strictly safer than `-y`
        # (YOLO, auto-approve everything) and silences the YOLO banner.
        argv = [
            self.cli_path,
            "-m", m,
            "-p", full_prompt,
            "--approval-mode", "plan",
            "-o", "text",
        ]

        def _call() -> str:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=600
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"gemini cli exit {proc.returncode}: {proc.stderr.strip()[:500]}"
                )
            if not proc.stdout.strip():
                raise RuntimeError("gemini cli returned empty output")
            return proc.stdout

        return _retry(_call, label="gemini cli")


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
        m = model or self.default_model
        # --bare keeps the system context minimal so summarization isn't polluted
        # by CLAUDE.md auto-discovery, hooks, plugin sync, etc.
        argv = [
            self.cli_path,
            "--print",
            "--model", m,
            "--system-prompt", system,
            "--bare",
            "--output-format", "text",
            *self.extra_args,
            prompt,
        ]

        def _call() -> str:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=600
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"claude cli exit {proc.returncode}: {proc.stderr.strip()[:500]}"
                )
            if not proc.stdout.strip():
                raise RuntimeError("claude cli returned empty output")
            return proc.stdout

        return _retry(_call, label="claude cli")


def query_with_validated_tags(
    client: LLMClient,
    system: str,
    prompt: str,
    required_tags: list[str],
    *,
    model: Optional[str] = None,
) -> str:
    """Like bases.query_llm_validated but takes an LLMClient. Retries once
    with an explicit reminder if the first response is missing required tags.
    """
    out = client.query(system, prompt, model=model)
    missing = [t for t in required_tags if not extract_tagged_contents(out, t)]
    if not missing:
        return out
    print(f"LLM output missing tags {missing}; retrying once with explicit reminder")
    reminder = (
        f"\n注意：上一次输出缺少必须的标签 {missing}。"
        f"请确保输出严格包含所有需要的标签：{required_tags}。\n"
    )
    out = client.query(system, prompt + reminder, model=model)
    still = [t for t in required_tags if not extract_tagged_contents(out, t)]
    if still:
        raise LLMError(f"output missing required tags after retry: {still}")
    return out


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

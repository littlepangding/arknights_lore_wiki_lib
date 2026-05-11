import json
import re
import os
from datetime import datetime
import hashlib
from typing import Optional

KEY_FILE = "keys.json"

RETRY_LIMIT = 5
RETRY_SLEEP_TIME = 60

DEFAULT_GAI_MODEL = "gemini-2.5-flash"
DEFAULT_CLI_MODEL = "gemini-3-flash-preview"

char_wiki_tags = [
    "version",
    "ID",
    "名称",
    "其他名称",
    "简要介绍",
    "相关角色",
    "详细介绍",
    "剧情高光",
    "战斗表现",
    "相关活动",
    "trivia",
    "角色点评",
]

CHAR_LLM_TAGS = [
    "名称",
    "其他名称",
    "简要介绍",
    "相关角色",
    "详细介绍",
    "剧情高光",
    "战斗表现",
    "相关活动",
    "trivia",
    "角色点评",
]

story_wiki_tags = [
    "version",
    "ID",
    "活动名称",
    "剧情总结",
    "剧情高光",
    "trivia",
    "关键人物",
    "角色剧情概括",
]

STORY_LLM_TAGS = [
    "剧情总结",
    "剧情高光",
    "trivia",
    "关键人物",
    "角色剧情概括",
]


def get_value(key, default=None):
    with open(KEY_FILE, "r") as f:
        data = json.load(f)
    return data.get(key, default)


def try_get_value(key, default=None):
    """`get_value` but tolerates a missing keys.json — useful for scripts
    invoked with all paths on the command line, before keys.json exists."""
    try:
        return get_value(key, default)
    except FileNotFoundError:
        return default


def build_llm_kwargs(llm_arg=None, model_arg=None):
    """Build the legacy-script LLM dispatch tuple from CLI args + keys.json.

    Honors per-backend model keys with `llm_model` as a shared fallback so
    a one-line keys.json override still works.

    Returns (backend, llm_kwargs, model). Callers pass `backend` to
    `query_llm` / `query_llm_validated` positionally and **-splat
    `llm_kwargs` for the rest. `llm_kwargs` deliberately omits the
    `backend` key — including it would collide with the positional arg.
    """
    backend = llm_arg or try_get_value("llm_backend", "cli")
    if backend == "cli":
        model = model_arg or try_get_value("llm_model", DEFAULT_CLI_MODEL)
        kwargs = {"model": model}
        cli_path = try_get_value("gemini_cli_path")
        if cli_path:
            kwargs["cli_path"] = cli_path
        return backend, kwargs, model
    if backend == "gai":
        from google import genai  # type: ignore[import-not-found]

        gai_client = genai.Client(api_key=try_get_value("genai_api_key"))
        model = (
            model_arg
            or try_get_value("gai_model")
            or try_get_value("llm_model")
            or DEFAULT_GAI_MODEL
        )
        return backend, {"gai_client": gai_client, "model": model}, model
    if backend == "claude":
        model = (
            model_arg
            or try_get_value("claude_model")
            or try_get_value("llm_model")
            or "claude-haiku-4-5"
        )
        kwargs = {"model": model}
        cli_path = try_get_value("claude_cli_path")
        if cli_path:
            kwargs["cli_path"] = cli_path
        return backend, kwargs, model
    raise ValueError(f"unknown llm backend: {backend!r}")


def ckp_stamp():
    """Creates a checkpoint with a timestamp."""
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H:%M:%S")
    return timestamp


def extract_tagged_contents(text, tag="TAG"):
    # Pattern to match <TAG>...</TAG> with newlines, non-greedy match
    pattern = re.compile(rf"[<\[]{tag}[\]>](.*?)[<\[]/{tag}[\]>]", re.DOTALL)
    return pattern.findall(text)


def _tag_block(tag, content):
    return f"<{tag}>\n{content.strip()}\n</{tag}>\n"


def validate_and_rebuild(txt, tags):
    lines = []
    for tag in tags:
        contents = extract_tagged_contents(txt, tag)
        if not contents:
            raise LLMError(f"output missing required tag <{tag}>")
        lines.append(_tag_block(tag, contents[0]))
    return "\n".join(lines)


# --- lenient tag-format repair ----------------------------------------------
# Models (gemini-3-flash-preview especially) routinely fumble the exact
# <tag>…</tag> shape on the *trailing* tag of a response: they open it and
# never close it, or drop into a markdown/colon "label: …" line instead.
# These helpers salvage those near-misses into canonical form before we spend
# tokens on a re-ask.

# Delimiters we'll tolerate around a tag name: half-width angle/square,
# full-width lenticular/double-angle/angle brackets.
_TAG_OPEN = r"[<\[【《〈]"
_TAG_CLOSE = r"[>\]】》〉]"
# A line that looks like the start of "the next thing" — a bracketed tag, a
# markdown header, or a bold label — used to bound a body that has no proper
# closing tag (unclosed-tag / label-form cases). The {1,24} caps the
# tag-name-ish run so a long prose line can't masquerade as a delimiter.
_NEXT_THINGISH = (
    r"(?:" + _TAG_OPEN + r"\s*/?\s*[^\n>\]】》〉]{1,24}" + _TAG_CLOSE
    + r"|#{1,6}\s|\*\*[^\n*]{1,24}\*\*)"
)


def _find_tag_content_lenient(text: str, tag: str) -> Optional[str]:
    """Best-effort extraction of one tag's body when the model didn't emit a
    clean ``<tag>…</tag>`` pair. Tries, in order:

    1. a bracketed pair, tolerant of bracket flavor and inner whitespace;
    2. an *open* bracket with no matching close — body runs to the next
       tag-ish line or end-of-text (the dominant "trailing tag" failure);
    3. a markdown header / bold / colon *label* line at line start
       (``### 场景标签``, ``**场景标签**``, ``场景标签：…``).

    Returns the stripped body, or ``None`` if even these miss. Only meant to be
    called for tags that strict matching already failed to find."""
    t = re.escape(tag)
    # 1. bracketed pair, loose delimiters + whitespace
    m = re.search(
        _TAG_OPEN + r"\s*" + t + r"\s*" + _TAG_CLOSE + r"(.*?)"
        + _TAG_OPEN + r"\s*/\s*" + t + r"\s*" + _TAG_CLOSE,
        text, re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    # 2. open bracket, no close: take to the next tag-ish line / EOF
    m = re.search(_TAG_OPEN + r"\s*" + t + r"\s*" + _TAG_CLOSE + r"(.*)", text, re.DOTALL)
    if m:
        body = m.group(1)
        nb = re.search(r"\n[ \t]*" + _NEXT_THINGISH, body)
        return (body[: nb.start()] if nb else body).strip()
    # 3. header / bold / colon "label" line at line start. Requires real
    #    decoration (a markdown prefix or a trailing colon) so a prose line
    #    that merely starts with the tag word isn't mistaken for the body.
    m = re.search(
        r"^[ \t]*(#{1,6}[ \t]*|\*{1,2}[ \t]*)?" + t
        + r"[ \t]*\*{0,2}[ \t]*([:：])?[ \t]*(.*)",
        text, re.DOTALL | re.MULTILINE,
    )
    if m and (m.group(1) or m.group(2)):
        body = m.group(3)
        nb = re.search(r"\n[ \t]*" + _NEXT_THINGISH, body)
        body = (body[: nb.start()] if nb else body).strip()
        if body:
            return body
    return None


def repair_tag_format(text: str, required_tags: list[str]) -> tuple[str, list[str]]:
    """If ``text`` already contains every required tag in clean
    ``<tag>…</tag>`` form, return ``(text, [])`` untouched. Otherwise salvage
    what we can via :func:`_find_tag_content_lenient` and return a rebuilt,
    canonicalized string (clean ``<tag>…</tag>`` blocks in ``required_tags``
    order) plus the tags we still couldn't find. The rebuilt form is exactly
    what :func:`validate_and_rebuild` would produce, so it's safe downstream."""
    if all(extract_tagged_contents(text, tag) for tag in required_tags):
        return text, []
    blocks: list[str] = []
    still_missing: list[str] = []
    for tag in required_tags:
        got = extract_tagged_contents(text, tag)
        content = got[0] if got else _find_tag_content_lenient(text, tag)
        if content is None:
            still_missing.append(tag)
            continue
        blocks.append(_tag_block(tag, content))
    return "\n".join(blocks), still_missing


# --- raw LLM-output archive -------------------------------------------------
# Every model response costs tokens; the validated summary we keep on disk is
# only a canonicalized subset (tags rebuilt, any extra prose dropped), and a
# response that failed validation is otherwise discarded entirely. So callers
# can opt into stashing the raw text for later reuse / triage. Off by default —
# CLI entrypoints enable it via `set_llm_archive_dir`; library code and tests
# leave it off so no stray files appear.

_llm_archive_dir: Optional[str] = None


def set_llm_archive_dir(path: Optional[str]) -> None:
    """Enable raw-LLM-output archiving to `path`. Falsy / empty → disabled."""
    global _llm_archive_dir
    _llm_archive_dir = str(path) if path else None


def archive_llm_output(label: str, content: str, *, kind: str = "out") -> Optional[str]:
    """Best-effort dump of one raw LLM response to
    ``<archive_dir>/<YYYY-MM-DD>/<label>__<kind>__<HHMMSS_micro>.txt``.
    No-op (returns ``None``) unless :func:`set_llm_archive_dir` enabled it.
    Never raises — a failed archive must not fail the work that produced it.
    Retention is manual (``rm -rf <archive_dir>/<date>``); no rotation."""
    root = _llm_archive_dir
    if not root or not content:
        return None
    try:
        now = datetime.now()
        day_dir = os.path.join(root, now.strftime("%Y-%m-%d"))
        os.makedirs(day_dir, exist_ok=True)
        safe_label = (re.sub(r"[^A-Za-z0-9._-]", "_", str(label))[:80] or "anon")
        safe_kind = re.sub(r"[^A-Za-z0-9._-]", "_", str(kind))[:32] or "out"
        path = os.path.join(
            day_dir, f"{safe_label}__{safe_kind}__{now.strftime('%H%M%S_%f')}.txt"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except OSError:
        return None


def get_txt_files(path):
    return [
        f
        for f in os.listdir(path)
        if f.endswith(".txt") and os.path.isfile(os.path.join(path, f))
    ]


def get_simple_filename(s):
    # Check if it's a valid simple filename: only a-zA-Z0-9
    if re.fullmatch(r"[a-zA-Z0-9_\.]+", s):
        return s
    else:
        # Hash the input and take first 6 hex digits
        h = hashlib.sha256(s.encode("utf-8")).hexdigest()[:6]
        return h


class LLMError(RuntimeError):
    pass


class LLMTerminalError(LLMError):
    """LLM failure that retrying cannot fix — wrong/missing model, exhausted
    quota, auth failure. Surfaces from `_retry` immediately and bubbles past
    `summarize_event`'s catch-all so the batch can bail instead of burning
    5 retries × N events against a wall."""
    pass


def _dispatch_legacy(backend, **kwargs):
    """Pop the (backend, **kwargs) signature shared by query_llm and
    query_llm_validated into an LLMClient + model pair."""
    # Imported here to avoid a circular import (llm_clients imports from this module).
    from libs.llm_clients import make_client

    model = kwargs.pop("model", None)
    client_kwargs = {}
    if backend == "gai":
        client_kwargs["gai_client"] = kwargs.pop("gai_client")
    elif backend in ("cli", "claude") and "cli_path" in kwargs:
        client_kwargs["cli_path"] = kwargs.pop("cli_path")
    if kwargs:
        raise TypeError(f"unexpected query_llm kwargs: {sorted(kwargs)}")
    return make_client(backend, **client_kwargs), model


def query_llm(backend, system_prompt, prompt_pre, prompt_post, text, **kwargs):
    """Dispatch to the configured backend.

    backend: "cli" (gemini CLI), "gai" (google.genai SDK), or "claude" (Claude CLI).
    For "gai" pass gai_client=... via kwargs. Optional: model, cli_path.
    Returns (raw_response_or_none, text) for backwards compatibility — the raw
    response slot is now always None since clients return text only.
    """
    client, model = _dispatch_legacy(backend, **kwargs)
    out = client.query(system_prompt, prompt_pre + text + prompt_post, model=model)
    return None, out


def query_llm_validated(
    backend, system_prompt, prompt_pre, prompt_post, text, required_tags, **kwargs
):
    """Like query_llm but retries once if the response is missing required tags."""
    from libs.llm_clients import query_with_validated_tags

    client, model = _dispatch_legacy(backend, **kwargs)
    return query_with_validated_tags(
        client,
        system_prompt,
        prompt_pre + text + prompt_post,
        required_tags,
        model=model,
    )

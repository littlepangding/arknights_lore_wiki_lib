import json
import re
import os
from datetime import datetime
import hashlib

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


def validate_and_rebuild(txt, tags):
    lines = []
    for tag in tags:
        contents = extract_tagged_contents(txt, tag)
        if not contents:
            raise LLMError(f"output missing required tag <{tag}>")
        lines.append(f"<{tag}>\n{contents[0].strip()}\n</{tag}>\n")
    return "\n".join(lines)


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

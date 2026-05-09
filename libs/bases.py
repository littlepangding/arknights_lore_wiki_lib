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
        assert (
            len(contents) >= 1
        ), f"{tag}\t{len(contents)}"  # f"{tag}\t{len(contents)}\n {txt}"
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


def query_llm(backend, system_prompt, prompt_pre, prompt_post, text, **kwargs):
    """Dispatch to the configured backend.

    backend: "cli" (gemini CLI), "gai" (google.genai SDK), or "claude" (Claude CLI).
    For "gai" pass gai_client=... via kwargs. Optional: model, cli_path.
    Returns (raw_response_or_none, text) for backwards compatibility — the raw
    response slot is now always None since clients return text only.
    """
    # Imported here to avoid a circular import (llm_clients imports from this module).
    from libs.llm_clients import make_client

    model = kwargs.pop("model", None)
    client_kwargs = {}
    if backend == "cli":
        if "cli_path" in kwargs:
            client_kwargs["cli_path"] = kwargs.pop("cli_path")
    elif backend == "gai":
        client_kwargs["gai_client"] = kwargs.pop("gai_client")
    elif backend == "claude":
        if "cli_path" in kwargs:
            client_kwargs["cli_path"] = kwargs.pop("cli_path")
    if kwargs:
        raise TypeError(f"unexpected query_llm kwargs: {sorted(kwargs)}")

    client = make_client(backend, **client_kwargs)
    full_prompt = prompt_pre + text + prompt_post
    out = client.query(system_prompt, full_prompt, model=model)
    return None, out


def query_llm_validated(
    backend, system_prompt, prompt_pre, prompt_post, text, required_tags, **kwargs
):
    """Like query_llm but retries once if the response is missing required tags.

    Catches a common LLM failure where output drops a required <tag> section,
    which would otherwise crash compile_website downstream.
    """
    _, out = query_llm(backend, system_prompt, prompt_pre, prompt_post, text, **kwargs)
    missing = [t for t in required_tags if not extract_tagged_contents(out, t)]
    if not missing:
        return out
    print(f"LLM output missing tags {missing}; retrying once with explicit reminder")
    reminder = (
        f"\n注意：上一次输出缺少必须的标签 {missing}。"
        f"请确保输出严格包含所有需要的标签：{required_tags}。\n"
    )
    _, out = query_llm(
        backend, system_prompt, prompt_pre + reminder, prompt_post, text, **kwargs
    )
    still_missing = [t for t in required_tags if not extract_tagged_contents(out, t)]
    if still_missing:
        raise LLMError(f"output missing required tags after retry: {still_missing}")
    return out

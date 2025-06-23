import json
import pickle
from collections import Counter
import re
import os
from datetime import datetime
import hashlib
import time

KEY_FILE = "keys.json"

RETRY_LIMIT = 5
RETRY_SLEEP_TIME = 60

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


def get_value(key):
    with open(KEY_FILE, "r") as f:
        data = json.load(f)
        return data.get(key, None)
    return None


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


def query_llm_gai(gai_client, system_prompt, prompt_pre, prompt_post, text):
    response, ret_text = None, None
    for it in range(RETRY_LIMIT):
        try:
            # Start streaming
            response = gai_client.models.generate_content(
                model="gemini-2.5-flash-preview-04-17",
                # config=types.GenerateContentConfig(
                #     max_output_tokens=max_output,
                # ),
                contents=system_prompt + prompt_pre + text + prompt_post,
            )
            ret_text = response.text
        except Exception as e:
            print(f"Query failed {e} \nwith text (len: {len(text)}): {text[:100]}")
            time.sleep(RETRY_SLEEP_TIME * (it + 1))
            continue
    return response, ret_text

import os
import sys
import argparse

from libs.bases import (
    build_llm_kwargs,
    get_value,
    ckp_stamp,
    query_llm_validated,
    get_simple_filename,
    STORY_LLM_TAGS,
)

from libs.game_data import (
    get_all_text_from_event,
    extract_data_from_story_review_table,
)


version = "v3"
force_gen_from_final_prompt = False


dir = ""
save_dir = ""
export_dir = ""
alias_filename = ""
wiki_path = ""

event_summary_system_prompt = """
你是一个很擅长总结文学作品的专家，而且你对于各种二次元手游文案很熟悉。 在总结时，你会尊重原著而不编造不存在的内容。 你的总结非常详细，清晰，易于阅读，同时也有一定的娱乐性。你会严格遵守格式输出要求，不添加额外的文字。你会严格的使用简体中文而不是繁体中文。
"""
event_summary_prompt_pre = """
以下是明日方舟这款游戏的一次活动的全部剧情文案，阅读时请注意以下几点：
- 剧情大部分以对话为主。有些图片信息并没有包含在提供的文案里，如果对于理解剧情有困难请适当进行猜测。
- 博士是游戏的主角，但是并不一定是每次活动的主角。有时候博士会有多个对话选择，他们一般来说是类似的含义，一般也不会影响剧情发展，不需要过度思考不同的选择。
- 每次活动的剧情并不是完整的，他可能有前传或者后传，不是所有的剧情都是可以解释的（有些是故意的悬念）。剧情也不一定没有矛盾的地方，不用太在意。 

在阅读剧情文本时，请根据要求的格式输出并完成相关的任务：
<剧情总结>
详细的剧情总结，至少1000字
</剧情总结>

<剧情高光>
选择一些剧情高光时刻，尽量引用原文
</剧情高光>

<trivia>
这次活动里有哪些有趣的trivia
</trivia>

<关键人物>
列出在活动中出现的重要人物（入选标准：如果我们要做一个这个人物的wiki，就需要提到这个活动里的相关剧情）。使用;分割， 
</关键人物>

<角色剧情概括>
对于每个关键人物，请总结他们在这次剧情里经历和作用
</角色剧情概括>

以下是剧情文案
"""

event_summary_prompt_post = """
以上是明日方舟这款游戏的一次活动的全部剧情文案，阅读时请注意以下几点：
- 剧情大部分以对话为主。有些图片信息并没有包含在提供的文案里，如果对于理解剧情有困难请适当进行猜测。
- 博士是游戏的主角，但是并不一定是每次活动的主角。有时候博士会有多个对话选择，他们一般来说是类似的含义，一般也不会影响剧情发展，不需要过度思考不同的选择。
- 每次活动的剧情并不是完整的，他可能有前传或者后传，不是所有的剧情都是可以解释的（有些是故意的悬念）。剧情也不一定没有矛盾的地方，不用太在意。 

在阅读剧情文本时，请根据要求的格式输出并完成相关的任务：
<剧情总结>
详细的剧情总结，至少1000字
</剧情总结>

<剧情高光>
选择一些剧情高光时刻，尽量引用原文
</剧情高光>

<trivia>
这次活动里有哪些有趣的trivia
</trivia>

<关键人物>
列出在活动中出现的重要人物（入选标准：如果我们要做一个这个人物的wiki，就需要提到这个活动里的相关剧情）。使用;分割， 
</关键人物>

<角色剧情概括>
对于每个关键人物，请总结他们在这次剧情里经历和作用
</角色剧情概括>
"""


def story_export(story_id, val, summary, version):
    headers = []
    headers.append(f"<time>{ckp_stamp()}</time>")
    headers.append(f"<version>{version}</version>")
    headers.append(f"<活动名称>{val['name']}</活动名称>")
    headers.append(f"<ID>{story_id}</ID>")
    headers.append(summary)
    return "\n".join(headers)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("story_id", help="story id")

    parser.add_argument("--wiki-path", default="")
    parser.add_argument("--game-data-path", default="")
    parser.add_argument(
        "--force", action="store_true", help="overwrite existing files or not"
    )
    parser.add_argument(
        "--llm",
        choices=["cli", "gai", "claude"],
        default=None,
        help="LLM backend; default reads keys.json llm_backend or 'cli'",
    )
    parser.add_argument("--model", default=None, help="model id, overrides default")
    args = parser.parse_args()

    backend, llm_kwargs, model = build_llm_kwargs(args.llm, args.model)
    print(f"param\t llm:{backend} model:{model}")

    wiki_path = args.wiki_path or get_value("lore_wiki_path")
    print(f"param\t wiki_path:{wiki_path}")
    site_path = os.path.join(wiki_path, "docs")
    print(f"param\t site_path:{site_path}")
    data_path = os.path.join(wiki_path, "data")
    print(f"param\t data_path:{data_path}")

    game_data_path = args.game_data_path or get_value("game_data_path")
    print(f"param\t game_data_path:{game_data_path}")

    story_review_data = extract_data_from_story_review_table(game_data_path)

    story_id = args.story_id
    assert story_id in story_review_data
    event_sum_path = os.path.join(data_path, "stories", f"{story_id}.txt")
    if os.path.exists(event_sum_path):
        print(
            f"{event_sum_path} exists for {story_id} {story_review_data[story_id]['name']}"
        )
        if args.force:
            print("will force override")
        else:
            print("will abort, use --force to override")
            sys.exit(0)
    text = get_all_text_from_event(game_data_path, story_review_data[story_id])
    print(
        f"Event Name: {story_review_data[story_id]['name']}, text length: {len(text)}"
    )

    full_response_gai = query_llm_validated(
        backend,
        system_prompt=event_summary_system_prompt,
        prompt_pre=event_summary_prompt_pre,
        prompt_post=event_summary_prompt_post,
        text=text,
        required_tags=STORY_LLM_TAGS,
        **llm_kwargs,
    )
    print(full_response_gai)

    txt = story_export(
        story_id, story_review_data[story_id], full_response_gai, version
    )
    filename = get_simple_filename(story_id)
    with open(os.path.join(data_path, "stories", filename + ".txt"), "w") as f:
        f.write(txt)

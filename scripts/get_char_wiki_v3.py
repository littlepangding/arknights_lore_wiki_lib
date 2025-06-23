import os
import pickle
from google import genai
from pypinyin import lazy_pinyin

import re

import argparse
from libs.bases import (
    get_value,
    ckp_stamp,
    get_txt_files,
    extract_tagged_contents,
    query_llm_gai,
)

from libs.game_data import (
    get_all_text_from_event,
    get_char_info_text_prompt,
    get_char_file_name,
    get_all_char_info,
    extract_data_from_story_review_table,
)


version = "v3"
force_gen_from_final_prompt = False


dir = ""
save_dir = ""
export_dir = ""
alias_filename = ""
wiki_path = ""

wiki_system_prompt = """
你是一个很擅长总结文学作品的专家，而且你对于各种二次元手游文案很熟悉。 在总结时，你会尊重原著而不编造不存在的内容。 你的总结非常详细，清晰，易于阅读，同时也有一定的娱乐性。你会严格遵守格式输出要求，不添加额外的文字。你会严格的使用简体中文而不是繁体中文。
"""


def get_story_key_chars(export_dir):
    path = os.path.join(export_dir, "stories")
    story_files = get_txt_files(path)
    print(f"load {len(story_files)} story summaries under {path}")
    ret = {}
    for f in story_files:
        data_file = os.path.join(export_dir, "stories", f)
        with open(data_file, "r") as f:
            txt = f.read()
        key_chars = extract_tagged_contents(txt, "关键人物")[0].strip()
        id = extract_tagged_contents(txt, "ID")[0].strip()
        ret[id] = [k.strip() for k in key_chars.split(":")]
    return ret


# Extract Event text from source files


def get_alias(char_name, alias_filename):
    with open(os.path.join(export_dir, alias_filename), "r") as f:
        txt = f.read()
    lines = [l for l in txt.split("\n") if l.strip()]
    for l in lines:
        names = [v for v in l.split(";") if v.strip()]
        if char_name in names:
            return names, char_name == names[0]
    return set([char_name]), True


def _is_related_event_name(name, story_text, key_chars, debug=False):
    cnt_thd = 1
    if story_text.count(f"{name}:") >= cnt_thd:
        if debug:
            print(1, story_text.count(f'"{name}":'))
        return True
    # cnt_thd = 3
    # if len(name) >= 2 and story_text.count(name) >= cnt_thd:
    #     if debug:
    #         print(2, story_text.count(name))
    #     return True
    for v2 in key_chars:
        if (name in v2 and len(name) >= 3) or (v2 == name):
            if debug:
                print(3, name, v2, key_chars)
            return True
    return False


def is_related_event_alias(alias, story_text, key_chars, debug=False):
    for name in alias:
        if _is_related_event_name(name, story_text, key_chars, debug=debug):
            return True
    return False


def get_related_events(alias, story_to_key_chars, full_story_text):
    related_event_ids = []
    for event_id in story_to_key_chars:
        if is_related_event_alias(
            alias,
            full_story_text[event_id],
            story_to_key_chars[event_id],
            # debug=event_id == "act27side",
        ):
            related_event_ids.append(event_id)
    return related_event_ids


# get a rough estimation for charactor if it is playable
def get_char_info_from_alias(alias, char_name_info):
    lines = []
    for char_name in alias:
        if char_name not in char_name_info:
            print(f"Did not find {char_name} in char_name_info")
            continue
        char_text = get_char_info_text_prompt(char_name_info[char_name])
        lines.append(char_text)

    return "\n".join(lines)


def get_event_summary_for_final_prompt(event_summary_for_char):
    lines = []
    for e_name, e_sum in event_summary_for_char:
        lines.append(f"<活动名称>{e_name}</活动名称>")
        lines.append(f"<相关内容>\n{e_sum}\n</相关内容>\n\n")
    return "\n".join(lines)


def get_final_prompt(char_name, alias, event_summary_for_char, char_name_info):
    lines = []
    lines.append(f"名称:{char_name}")
    lines.append(f"已知其他名称(可能有更多):{";".join(list(alias))}")
    char_text = get_char_info_from_alias(alias, char_name_info)
    if char_text:
        lines.append(f"<角色的所有档案>\n{char_text}\n</角色的所有档案>")
    if event_summary_for_char:
        e_sum = get_event_summary_for_final_prompt(event_summary_for_char)
        lines.append(f"<所有相关的活动剧情总结>\n{e_sum}\n</所有相关的活动剧情总结>")
    return "\n".join(lines)


def get_event_summary_from_final_prompt(final_prompt):
    related_story_info_text = extract_tagged_contents(
        final_prompt, tag="所有相关的活动剧情总结"
    )[0]
    event_names = extract_tagged_contents(related_story_info_text, tag="活动名称")
    event_sums = extract_tagged_contents(related_story_info_text, tag="相关内容")
    assert len(event_names) == len(event_sums)
    return list(zip(event_names, event_sums))


def main(char_name, char_name_info, story_data, force=False, version=None):
    # initial processing of which steps to run
    file_name = get_char_file_name(char_name, char_name_info)
    final_results_path = os.path.join(export_dir, "char_v3", file_name + ".txt")
    if (
        not force
        and not force_gen_from_final_prompt
        and os.path.exists(final_results_path)
    ):
        print(f"{final_results_path} existed")
        return

    use_existing_final_prompt = False
    final_prompt_path = os.path.join(
        export_dir, "char_v3", "prompt_" + file_name + ".txt"
    )
    if not force and os.path.exists(final_prompt_path):
        print(f"Existing final prompt found, will process on top of it")
        use_existing_final_prompt = True

    # prepare for getting related events
    story_to_key_chars = get_story_key_chars(export_dir)
    print(f"story_to_key_chars len: {len(story_to_key_chars)}")

    full_story_text = {}
    for k, val in story_data.items():
        text = get_all_text_from_event(game_data_path, val)
        full_story_text[k] = text
    print(f"loaded {len(full_story_text)} full story text")

    # get alias

    alias, is_main = get_alias(char_name, alias_filename)
    if not is_main:
        print(f"Skipping {char_name} {alias}")
        return
    print(f"Get the alias for {char_name}: {alias}")
    related_event_ids = get_related_events(alias, story_to_key_chars, full_story_text)
    print(f"total number of events: {len(related_event_ids)}")
    print(
        f"total event text length: {sum(len(full_story_text[v]) for v in related_event_ids)}"
    )
    for e in related_event_ids:
        print(e, story_data[e]["name"])

    # prepare for STEP 2
    if use_existing_final_prompt:
        with open(final_prompt_path, "r") as f:
            existing_final_prompt = f.read()
        event_summary_for_char = get_event_summary_from_final_prompt(
            existing_final_prompt
        )
        remaining_related_event_ids = []
        existing_event_names = {e: s for e, s in event_summary_for_char}
        for e in related_event_ids:
            e_name = story_data[e]["name"]
            if e_name in existing_event_names:
                print(
                    f"Found summary of {e_name} in existing final prompt:\n{existing_event_names[e_name]}"
                )
            else:
                print(
                    f"Did not find summary of {e_name} in existing final prompt, will generate it"
                )
                remaining_related_event_ids.append(e)
        related_event_ids = remaining_related_event_ids
    else:
        event_summary_for_char = []

    if (not related_event_ids) and (not force_gen_from_final_prompt):
        print(f"No event to process and no force gen from final prompt, exiting")
        return

    wiki_format = f"""
    wiki的输出格式如下. 请控制wiki的总长度大概在5000字以内。

    <名称>
    {char_name}
    </名称>

    <其他名称>
    这个角色的其他称呼，可以是自称，别人起的绰号等等。只包括确定性高的称呼，不要包括不常用，不靠谱的信息。使用;分割
    </其他名称>

    <简要介绍>
    200字左右的简要角色介绍，包括角色来历，阵营，关键经历
    </简要介绍>

    <相关角色>
    和这个角色相关的其他角色,以及他们之间的相关剧情。注意，如果一些角色的相关性非常的低，或者相关性过于平凡，请不要包括进来。"相关的角色名"请使用简单标准的名称，不要随意添加绰号部分。请注意不要将同一个角色分成多个，请合理的总结推测合并重复项。
    分行写：
    相关角色名：相关剧情
    </相关角色>

    <详细介绍>
    1000字以上的详细角色介绍，剧情和人设相关内容为主。在合并时请注意不同剧情的详略问题和时间顺序。
    </详细介绍>

    <剧情高光>
    选择一些剧情高光时刻。这里请仔细判断是否是真正的高光，不要加入一些很平淡无奇无趣的内容。尽量引用原文, 并表明出处(比如出自档案还是模组？如果是来源是某个活动的话，相关的活动名称/章节名称)。
    </剧情高光>

    <战斗表现>
    角色的战斗力描述和在剧情中的战力表现，尽量引用原文, 并表明出处(比如出自档案还是模组？如果是来源是某个活动的话，相关的活动名称/章节名称)。
    </战斗表现>

    <相关活动>
    列出和这个角色相关的活动名称和这个角色在这个活动中的相关剧情。注意，如果一些活动的相关性非常的低，或者相关性过于平凡，请不要包括进来。"活动名称"请使用简单标准的名称，不要随意更改。分行写
    活动名称：相关剧情
    </相关活动>

    <trivia>
    这个角色的有趣trivia；选择最有趣的内容，分行写。尽量引用原文, 并表明出处(比如出自档案还是模组？如果是来源是某个活动的话，相关的活动名称/章节名称)。
    </trivia>

    <角色点评>
    客观但是富有情感的点评这个角色
    </角色点评>

    """

    prompt_step1 = f"""
    以下是明日方舟这款游戏的中的一个干员/角色的目前档案， 请总结他们的内容并按照以下提供的wiki格式进行输出。
    - 这次提供的信息可能包括多个档案，对应同一个干员/角色的不同时期的资料，请将她们总结起来。<名称>已经提供在输出格式中，请不要修改
    - 博士是游戏的主角，但是并不一定是每次活动的主角。
    - <干员语音> 是这个干员对于博士的语音，或者是战斗时的语音，不一定有明确的剧情意义，请注意甄别。
    - 每次活动的剧情并不是完整的，他可能有前传或者后传，不是所有的剧情都是可以解释的（有些是故意的悬念）。剧情也不一定没有矛盾的地方，不用太在意。 
    - 这次提供的信息比较简单，还没有包括具体的活动剧情，所以在输出wiki时以简单准确为主，不需要写很长，要简短准确！


    输出时，请注意：
    - 因为输出是一个wiki页面，所以请不要编造内容，要严格根据提供的文本生成信息

    {wiki_format}

    以下是干员/角色的信息:
    """

    ########## STEP 1 (optional) get a rough estimation for charactor if it is playable

    if related_event_ids:
        char_info_step1 = get_char_info_from_alias(alias, char_name_info)
        wiki_step1 = ""
        if char_info_step1:
            print("Step 1: getting initial char wiki from records")
            print(f"prompt length ~ {len(char_info_step1)}")
            response_gai, full_response_gai = query_llm_gai(
                gai_client,
                system_prompt=wiki_system_prompt,
                prompt_pre=prompt_step1,
                prompt_post="",
                text=char_info_step1,
            )
            wiki_step1 = full_response_gai
            print(f"Step 1:\ncurrent wiki:\n{wiki_step1}")

        else:
            print(
                "Skipping step 1 since there is no record (likely not playable charactor)"
            )
    else:
        print("Skipping step 1 since there is no related events to be processed")

    ########## Step 2: for all events, get summary from full event text regarding this charactor

    main_prompt_step2 = """是明日方舟这款游戏的中的一个干员/角色的名称以及基本信息，和一个活动的完整剧情文案， 请总结这个活动中和这个角色相关的剧情，并按规定的格式输出。
    - 一个干员/角色可能有不同的称呼，请认真判断剧情是否和这个角色有关，不要进行不必要的猜测。
    - 博士是游戏的主角，但是并不一定是每次活动的主角。
    - 每次活动的剧情并不是完整的，他可能有前传或者后传，不是所有的剧情都是可以解释的（有些是故意的悬念）。剧情也不一定没有矛盾的地方，不用太在意。 
    - 文本中包括提供的活动剧情，或者活动剧情的总结。这些活动不一定是根据时间顺序排列的，请不要假设这一点，而是根据文本内容进行适当的猜测。
    - 如果这个活动和这个角色没有直接的关系，可以输出空白的内容。不要编造不存在的关系。

    输出时，请注意：
    - 因为这里的信息会被最终用于生成一个wiki页面，所以请不要编造内容，要严格根据提供的文本生成信息
    - 这个活动有可能和这个干员/角色并无直接关系，请认真区分，不要编造不存在的关系。如果两者并不相关，可以根据指示留空。

    输出格式如下. 请控制总长度大概在5000字以内。

    <相关剧情总结>
    和提供的角色相关的剧情，注意详略得当。相关性低的话，可以只用很少的字数。如果没有关系的话，也可以留空。
    </相关剧情总结>

    <相关剧情高光>
    和提供的角色相关的剧情高光，注意详略得当。相关性低的话，可以只用很少的字数。如果没有关系的话，也可以留空。尽量引用原文, 并表明出处(活动名称/章节名称)。
    </相关剧情高光>

    <相关角色总结>
    剧情中有哪些和提供的角色相关的其他角色，她们之间的关系如何，注意详略得当。相关性低的话，可以只用很少的字数。如果没有关系的话，也可以留空。
    </相关角色总结>

    <相关trivia>
    和提供的角色相关的有趣trivia，注意详略得当。相关性低的话，可以只用很少的字数。如果没有关系的话，也可以留空。选择最有趣的内容，分行写。尽量引用原文, 并表明出处(活动名称/章节名称)
    </相关trivia>
    """
    prompt_step2_pre = f"""
    以下{main_prompt_step2}

    以下是输入信息:
    """
    prompt_step2_post = f"""
    以上{main_prompt_step2}
    """

    print(f"Step 2: summarizing event for character")

    for e in related_event_ids:
        e_name = story_data[e]["name"]
        print(f"Step 2: summarizing event {e_name}")
        full_text = full_story_text[e]
        prompt_text = f"""
        角色名称:{char_name}
        其他名称:{";".join(list(alias))}

        {"基本的角色信息(用于参考):\n{wiki_step1}" if wiki_step1 else ""}

        完整活动剧情文本:
        {full_text}
        """
        print(f"prompt length ~ {len(prompt_text)}")
        response_gai, full_response_gai = query_llm_gai(
            gai_client,
            system_prompt=wiki_system_prompt,
            prompt_pre=prompt_step2_pre,
            prompt_post=prompt_step2_post,
            text=prompt_text,
        )
        print(f"output: \n{full_response_gai}")
        event_summary_for_char.append((e_name, full_response_gai))

    ########## Step 3: summarizing the wiki based on all information
    main_prompt_step3 = f"""是明日方舟这款游戏的中的一个干员/角色的目前档案信息和所有相关的剧情的内容总结， 请总结他们的内容并按照以下提供的wiki格式进行输出。
    - 这次提供的信息可能包括多个档案，对应同一个干员/角色的不同时期的资料，请将她们总结起来。<名称>已经提供在输出格式中，请不要修改
    - 博士是游戏的主角，但是并不一定是每次活动的主角。
    - <干员语音> 是这个干员对于博士的语音，或者是战斗时的语音，不一定有明确的剧情意义，请注意甄别。
    - 每次活动的剧情并不是完整的，他可能有前传或者后传，不是所有的剧情都是可以解释的（有些是故意的悬念）。剧情也不一定没有矛盾的地方，不用太在意。 
    - 文本中包括提供的活动剧情，或者活动剧情的总结。这些活动不一定是根据时间顺序排列的，请不要假设这一点，而是根据文本内容进行适当的猜测。


    输出时，请注意：
    - 因为输出是一个wiki页面，所以请不要编造内容，要严格根据提供的文本生成信息
    - 在总结和合并的过程中请详略得当，用你的聪明才智判断哪些信息更合适放在最终的合并版wiki中。

    {wiki_format}

    """

    prompt_step3_pre = f"""
    以下{main_prompt_step3}

    以下是输入信息:
    """

    prompt_step3_post = f"""
    以上{main_prompt_step3}

    """

    print(f"Step 3: summarizing the wiki based on all information")

    print(f"Step 3: using base name {file_name}")
    final_prompt = get_final_prompt(
        char_name, alias, event_summary_for_char, char_name_info
    )

    print(
        f"Step 3: saving the final prompt (len: {len(final_prompt)}) to {final_prompt_path}"
    )
    with open(final_prompt_path, "w") as f:
        f.write(final_prompt)
    response_gai, full_response_gai = query_llm_gai(
        gai_client,
        system_prompt=wiki_system_prompt,
        prompt_pre=prompt_step3_pre,
        prompt_post=prompt_step3_post,
        text=final_prompt,
    )
    print(f"output final:\n{full_response_gai}")

    print(f"Step 3: saving the final results to {final_results_path}")
    headers = []
    headers.append(f"<time>{ckp_stamp()}</time>")
    headers.append(f"<version>{version}</version>")
    headers.append(f"<ID>{file_name}</ID>")
    headers.append(full_response_gai)
    with open(final_results_path, "w") as f:
        f.write("\n".join(headers))


if __name__ == "__main__":

    # initial genai client
    gai_client = genai.Client(api_key=get_value("genai_api_key"))

    parser = argparse.ArgumentParser()
    parser.add_argument("char", help="char name")
    parser.add_argument("--wiki-path", default="")
    parser.add_argument("--game-data-path", default="")
    parser.add_argument(
        "--force", action="store_true", help="overwrite existing files or not"
    )
    parser.add_argument(
        "--force-final",
        action="store_true",
        help="regen final file even if final prompt is not changed",
    )
    parser.add_argument("--version", default=None, help="the version for this update")

    args = parser.parse_args()

    wiki_path = args.wiki_path or get_value("lore_wiki_path")
    print(f"param\t wiki_path:{wiki_path}")
    dir = os.path.join(wiki_path, "zh_CN")
    print(f"param\t dir:{dir}")
    export_dir = os.path.join(wiki_path, "data")
    print(f"param\t export_dir:{export_dir}")

    game_data_path = args.game_data_path or get_value("game_data_path")
    print(f"param\t game_data_path:{game_data_path}")

    save_dir = get_value("save_path_to_depre")
    print(f"param\t save_dir:{save_dir}")

    alias_filename = "char_alias.txt"
    print(f"param\t alias_filename:{alias_filename}")

    force_gen_from_final_prompt = args.force_final

    char_info, char_name_info = get_all_char_info(game_data_path)
    story_data = extract_data_from_story_review_table(game_data_path)

    main(args.char, char_name_info, story_data, args.force, args.version)

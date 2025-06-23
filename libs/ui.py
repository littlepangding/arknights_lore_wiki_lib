import os
from libs.bases import (
    extract_tagged_contents,
    validate_and_rebuild,
    get_txt_files,
    char_wiki_tags,
    story_wiki_tags,
)
from pypinyin import lazy_pinyin


char_wiki_tags_out = [
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
story_wiki_tags_out = [
    "剧情总结",
    "剧情高光",
    "trivia",
    "关键人物",
    "角色剧情概括",
]
WARNING = """ 

| :warning: 注意！本页面是利用LLM阅读总结明日方舟剧情原文生成，具体方法请看repo的PR history或者b站视频：[BV1gdJ7zqESe](https://www.bilibili.com/video/BV1gdJ7zqESe/)         |
|:----------------------------|
| 虽然在生成的过程中已尽量避免，但是错误，幻觉等等仍然无法完全避免。所以本页面内容以娱乐为主，切勿当成一手来源。发现错误请open issue或者b站私信作者进行修改。|


"""


def list_to_markdown_table(items, num_columns):
    if num_columns <= 0:
        raise ValueError("Number of columns must be greater than 0.")

    rows = []
    for i in range(0, len(items), num_columns):
        row = items[i : i + num_columns]
        # Pad the row if it's shorter than num_columns
        if len(row) < num_columns:
            row += [""] * (num_columns - len(row))
        rows.append(row)

    lines = []

    lines.append("  ".join(["|"] * (num_columns + 1)))
    lines.append(" --- ".join(["|"] * (num_columns + 1)))
    for row in rows:

        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def parse_lines_to_bold_list(txt, n2d=None):
    """
    n2d: name to direct link
    """
    lis = [v.strip() for v in txt.split("\n")]
    lines = []
    for v in lis:
        if len(v) == 0:
            continue
        v = v.replace("*", "")
        idx = max(v.find(":"), v.find("："))
        if idx > 0:
            first = v[:idx].strip()
            if n2d and first in n2d:
                first = f"**{n2d[first]}**"
            else:
                first = f"**{first}**"
            lines.append(first + v[idx:])
        else:
            lines.append(v)

    return "\n".join(["-   " + l for l in lines])


def get_char_md_page(data_file, n2d_c=None, n2d_s=None):
    with open(data_file, "r") as f:
        txt = f.read()
    txt = validate_and_rebuild(txt, char_wiki_tags)
    output_filename = extract_tagged_contents(txt, "ID")[0].strip() + ".md"
    char_name = extract_tagged_contents(txt, "名称")[0].strip().replace("\n", "")
    lines = []
    lines.append(f"# {char_name}")
    lines.append(f"页面版本:{extract_tagged_contents(txt, "version")[0].strip()}")
    lines.append(WARNING)
    for tag in char_wiki_tags_out:
        tag_content = extract_tagged_contents(txt, tag)[0].strip()
        if tag in [
            "相关角色",
            "相关活动",
        ]:
            tag_content = parse_lines_to_bold_list(
                tag_content,
                n2d=n2d_c if tag == "相关角色" else n2d_s,
            )
        lines.append(f"## {tag}\n{tag_content}")
    out = "\n".join(lines)
    return char_name, output_filename, out


def output_char_wikis(data_subdir, site_subdir, force=True, n2d_c=None, n2d_s=None):
    files = get_txt_files(data_subdir)
    len(files)

    force = True
    index = []
    files_with_issues = []
    for f in files:
        # print(f)
        if f.startswith("prompt"):
            continue
        if f.startswith("depre"):
            continue
        try:
            name, _, out = get_char_md_page(
                os.path.join(data_subdir, f),
                n2d_c,
                n2d_s,
            )
            out_file = f.replace(".txt", ".md")
            index.append((name, out_file))
            out_path = os.path.join(site_subdir, out_file)
            if not force and os.path.exists(out_path):
                print(f"{out_path} existed")
                continue
            with open(out_path, "w") as f:
                f.write(out)
        except Exception as e:
            print(f)
            print(e)
            files_with_issues.append(f)
    return index, files_with_issues


def get_char_name_and_display(index_v1, index_v3, rel_dir_v1, re_dir_v3):
    all_names = set(k[0] for k in index_v1).union(set(k[0] for k in index_v3))
    ind1 = {k: v for k, v in index_v1}
    ind3 = {k: v for k, v in index_v3}
    n2d_p = {}
    n2d_np = {}
    for name in all_names:
        vs = []
        f_name = ""
        v1_f = ind1.get(name, "")
        if v1_f:
            v1 = f"[v1]({rel_dir_v1}{v1_f})"
            vs.append(v1)
            f_name = v1_f
        v3_f = ind3.get(name, "")
        if v3_f:
            v3 = f"[v2]({re_dir_v3}{v3_f})"
            vs.append(v3)
        f_name = v3_f or v1_f
        if f_name.startswith("char"):
            n2d_p[name] = f"{name}({','.join(vs)})"
        elif f_name.startswith("extended"):
            n2d_np[name] = f"{name}({','.join(vs)})"
    return n2d_p, n2d_np


def get_char_name_and_display_second(
    index_v1, index_v3, rel_dir_v1, rel_dir_v3, version="v1"
):
    all_names = set(k[0] for k in index_v1).union(set(k[0] for k in index_v3))
    ind1 = {k: v for k, v in index_v1}
    ind3 = {k: v for k, v in index_v3}
    n2d_p = {}
    n2d_np = {}
    for name in all_names:
        if name not in ind3:
            continue
        vs = []
        f_name = ind3.get(name, "")
        v3_f = ind3.get(name, "")
        disp = f"[{name}]({rel_dir_v3}{v3_f})"
        v1_f = ind1.get(name, "")
        if v1_f:
            disp += f"([{version}]({rel_dir_v1}{v1_f}))"
        if f_name.startswith("char"):
            n2d_p[name] = disp
        elif f_name.startswith("extended"):
            n2d_np[name] = disp
    return n2d_p, n2d_np


def output_char_index_page_v1(n2d_p, n2d_np, n2d_p_o, n2d_np_o):

    n1p = sum([v.find("v1") >= 0 for _, v in n2d_p.items()])
    n2p = len(n2d_p)  # sum([v.find("v2") >= 0 for _, v in n2d_p.items()])
    n1np = sum([v.find("v1") >= 0 for _, v in n2d_np.items()])
    n2np = len(n2d_np)  # sum([v.find("v2") >= 0 for _, v in n2d_np.items()])
    lines = []
    lines.append(f"# 明日方舟剧情wiki 角色总表")
    lines.append(WARNING)
    lines.append(
        f"""
    目前已知问题（会在将来尽量fix）：
    -  目前总共{n1p+n1np}(版本1/v1)/{n2p+n2np}(版本2/v2)角色, 但是角色可能有重复（比如异格或者别名）；
    -  目前默认版本已经是版本二。版本一已经不再更新。
    """
    )

    def _get_contents(val):
        tmp_v = sorted(
            [(k, v) for k, v in val.items()], key=lambda x: lazy_pinyin(x[0])
        )
        return list_to_markdown_table([d for _, d in tmp_v], 5)

    lines.append(f"## 干员 (共计:{len(n2d_p)})")

    lines.append(_get_contents(n2d_p))

    lines.append(f"### 其他剧情角色 (共计:{len(n2d_np)})")
    lines.append(_get_contents(n2d_np))

    lines.append(f"## 曾出现在版本一中的角色 ")
    for k in n2d_p:
        n2d_p_o.pop(k)
    for k in n2d_np:
        n2d_np_o.pop(k)

    lines.append(f"### 干员 (共计:{len(n2d_p_o)})")
    lines.append(_get_contents(n2d_p_o))

    lines.append(f"### 其他剧情角色 (共计:{len(n2d_np_o)})")
    lines.append(_get_contents(n2d_np_o))

    return "\n".join(lines)


def get_story_md_page(data_file, n2d=None):
    with open(data_file, "r") as f:
        txt = f.read()
    txt = validate_and_rebuild(txt, story_wiki_tags)
    output_filename = extract_tagged_contents(txt, "ID")[0].strip() + ".md"
    char_name = extract_tagged_contents(txt, "活动名称")[0].strip().replace("\n", "")
    lines = []
    lines.append(f"# {char_name}")
    lines.append(f"页面版本:{extract_tagged_contents(txt, "version")[0].strip()}")
    lines.append(WARNING)
    for tag in story_wiki_tags_out:
        tag_content = extract_tagged_contents(txt, tag)[0].strip()
        if tag in [
            "角色剧情概括",
        ]:
            tag_content = parse_lines_to_bold_list(tag_content, n2d)
        lines.append(f"## {tag}\n{tag_content}")
    out = "\n".join(lines)
    return char_name, output_filename, out


def output_story_wiki(data_subdir="stories", site_subdir="stories", n2d=None):
    story_files = get_txt_files(data_subdir)
    len(story_files)
    # write to the indivudual page
    force = True
    index = []
    for f in story_files:
        # print(f)
        name, _, out = get_story_md_page(os.path.join(data_subdir, f), n2d)
        out_file = f.replace(".txt", ".md")
        index.append((name, out_file))
        out_path = os.path.join(site_subdir, out_file)
        if not force and os.path.exists(out_path):
            print(f"{out_path} existed")
            continue
        with open(out_path, "w") as f:
            f.write(out)
    return index


def get_char_name_from_story(n, story_to_char):
    if n in story_to_char:
        return f"({story_to_char[n]})"
    else:
        return ""


def output_story_index_page(index, story_review_data, story_to_char):

    lines = []
    lines.append(f"# 明日方舟剧情wiki 活动总表")
    lines.append(WARNING)
    lines.append(
        f"""
    目前已知问题（会在将来尽量fix）：
    -  部分页面有已知幻觉；
    -  SS/主线/故事集排序
    """
    )

    def get_story_type(f):
        n = f[: f.find(".md")]
        # print(n)
        entryType = story_review_data[n]["entryType"]
        return entryType

    type_pair = [
        ("MAINLINE", "主线"),
        ("ACTIVITY", "Side Story"),
        ("MINI_ACTIVITY", "故事集"),
        ("NONE", "其他"),
    ]

    for e_type, type_n in type_pair:
        ind1 = [i for i in index if get_story_type(i[1]) == e_type]
        lines.append(f"## {type_n} (共计:{len(ind1)})")
        ind1 = sorted(ind1, key=lambda x: lazy_pinyin(x[0]))

        contents1 = [
            f"[{n}](stories/{f}){get_char_name_from_story(n, story_to_char)}" for n, f in ind1
        ]
        lines.append(list_to_markdown_table(contents1, 5))

    return "\n".join(lines)

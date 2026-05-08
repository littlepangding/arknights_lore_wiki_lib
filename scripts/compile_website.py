import os
import argparse

from libs import bases
from libs.ui import (
    output_char_wikis,
    get_char_name_and_display_second,
    get_char_name_and_display,
    output_char_index_page_v1,
    output_story_wiki,
    output_story_index_page,
    get_char_name_from_story,
)
from libs.game_data import (
    extract_data_from_story_review_table,
    get_all_char_info,
)


def _read_list_file(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return [l.strip() for l in f if l.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-path", default="")
    parser.add_argument("--game-data-path", default="")
    parser.add_argument(
        "--new-stories-file",
        default=None,
        help="path to a file with one story_id per line (e.g. tmp/stories_<date>.txt). "
        "Used to print the README 'what's new' snippet at the end.",
    )
    parser.add_argument(
        "--new-chars-file",
        default=None,
        help="path to a file with one canonical char name per line (e.g. tmp/char_<date>.txt). "
        "Used to print the README 'what's new' snippet at the end.",
    )

    args = parser.parse_args()
    new_stories = _read_list_file(args.new_stories_file)
    new_chars = _read_list_file(args.new_chars_file)

    wiki_path = args.wiki_path or bases.get_value("lore_wiki_path")
    print(f"param\t wiki_path:{wiki_path}")
    site_path = os.path.join(wiki_path, "docs")
    print(f"param\t site_path:{site_path}")
    data_path = os.path.join(wiki_path, "data")
    print(f"param\t data_path:{data_path}")

    game_data_path = args.game_data_path or bases.get_value("game_data_path")
    print(f"param\t game_data_path:{game_data_path}")

    story_review_data = extract_data_from_story_review_table(game_data_path)

    ### TODO replace

    char_info, char_name_info = get_all_char_info(game_data_path)
    # save_dir = "/home/pangdd/Codes/LocalAIExps/arknight_story_wiki/"
    # with open(os.path.join(save_dir, "char_info_2025-05-16-20:05:00.pkl"), "rb") as f:
    #     data = pickle.load(f)
    #     # {"char_info": char_info, "char_name_info": char_name_info}
    #     char_info = data["char_info"]
    #     char_name_info = data["char_name_info"]
    story_to_char = {}
    for name, val in char_name_info.items():
        if "storysets" not in val:
            continue
        for v1 in val["storysets"]:
            story_to_char[v1["storySetName"]] = name
    ### TODO

    # initial version of v1 char pages
    char_data_dir_v1 = "chars"
    char_site_dir_v1 = "chars"

    index_v1, f_issues_v1 = output_char_wikis(
        os.path.join(data_path, char_data_dir_v1),
        os.path.join(site_path, char_site_dir_v1),
        force=True,
    )
    print(f_issues_v1)

    ######## # Export Char pages (without links between them) and char index page
    # initial version of v3 char pages
    char_data_dir_v3 = "char_v3"
    char_site_dir_v3 = "char_v3"

    index_v3, f_issues_v3 = output_char_wikis(
        os.path.join(data_path, char_data_dir_v3),
        os.path.join(site_path, char_site_dir_v3),
        force=True,
    )
    print(f_issues_v3)

    # output more compact char wiki index page
    n2d_p, n2d_np = get_char_name_and_display_second(
        index_v1, index_v3, "chars/", "char_v3/"
    )
    n2d_p_old, n2d_np_old = get_char_name_and_display(
        index_v1, index_v3, "chars/", "char_v3/"
    )
    with open(os.path.join(site_path, "char_index.md"), "w") as f:
        f.write(output_char_index_page_v1(n2d_p, n2d_np, n2d_p_old, n2d_np_old))

    ######## # export story pages and story index pages
    story_data_subdir = "stories"
    story_site_subdir = "stories"
    index_s = output_story_wiki(
        os.path.join(data_path, story_data_subdir),
        os.path.join(site_path, story_site_subdir),
    )

    # write to the index page
    with open(os.path.join(site_path, "story_index.md"), "w") as f:
        f.write(output_story_index_page(index_s, story_review_data, story_to_char))

    ######### add links between pages
    # v1 char wiki page with link to other chars
    n2d_p, n2d_np = get_char_name_and_display_second(
        index_v1, index_v3, "", "../char_v3/"
    )
    n2d_p.update(n2d_np)
    n2d_s = {k: f"[{k}](../stories/{v})" for k, v in index_s}

    char_data_dir_v1 = "chars"
    char_site_dir_v1 = "chars"

    index_v1, f_issues_v1 = output_char_wikis(
        os.path.join(data_path, char_data_dir_v1),
        os.path.join(site_path, char_site_dir_v1),
        force=True,
        n2d_c=n2d_p,
        n2d_s=n2d_s,
    )
    print(f_issues_v1)

    # v3 char wiki page with link to other chars
    n2d_p, n2d_np = get_char_name_and_display_second(
        index_v1, index_v3, "../chars/", ""
    )
    n2d_p.update(n2d_np)
    n2d_s = {k: f"[{k}](../stories/{v})" for k, v in index_s}

    char_data_dir_v3 = "char_v3"
    char_site_dir_v3 = "char_v3"

    index_v3, f_issues_v3 = output_char_wikis(
        os.path.join(data_path, char_data_dir_v3),
        os.path.join(site_path, char_site_dir_v3),
        force=True,
        n2d_c=n2d_p,
        n2d_s=n2d_s,
    )
    print(f_issues_v3)

    n2d_p, n2d_np = get_char_name_and_display_second(
        index_v1, index_v3, "../chars/", "../char_v3/"
    )
    n2d_p.update(n2d_np)

    index_s = output_story_wiki(
        os.path.join(data_path, story_data_subdir),
        os.path.join(site_path, story_site_subdir),
        n2d=n2d_p,
    )

    story_dict = {k: v for k, v in index_s}
    story_new_parts = []
    for new_story in new_stories:
        if new_story not in story_review_data:
            print(f"WARN: new story {new_story!r} not in story_review_data; skipping")
            continue
        name = story_review_data[new_story]["name"]
        if name not in story_dict:
            print(f"WARN: story {new_story} ({name}) has no compiled page; skipping")
            continue
        story_new_parts.append(
            f"[{name}](docs/stories/{story_dict[name]})"
            f"{get_char_name_from_story(name, story_to_char)}"
        )
    story_new_txt = ", ".join(story_new_parts)

    char_dict = {k: v for k, v in index_v3}
    char_new_parts = []
    for new_char in new_chars:
        if new_char not in char_dict:
            print(f"WARN: char {new_char!r} has no compiled page; emitting plain text")
            char_new_parts.append(new_char)
        else:
            char_new_parts.append(f"[{new_char}](docs/char_v3/{char_dict[new_char]})")
    char_new_txt = ", ".join(char_new_parts)

    print()
    print("=" * 60)
    print("README 'what's new' snippet (paste into arknights_lore_wiki/README.md):")
    print("=" * 60)
    if story_new_txt:
        print(f"\n更新剧情- {story_new_txt}")
    if char_new_txt:
        print(f"\n新增/更新角色 {char_new_txt}")

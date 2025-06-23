import os
from libs import bases
from libs.game_data import (
    get_all_char_info,
)

import argparse


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-path", default="")
    parser.add_argument("--game-data-path", default="")

    args = parser.parse_args()

    wiki_path = args.wiki_path or bases.get_value("lore_wiki_path")
    print(f"param\t wiki_path:{wiki_path}")
    site_path = os.path.join(wiki_path, "docs")
    print(f"param\t site_path:{site_path}")
    data_path = os.path.join(wiki_path, "data")
    print(f"param\t data_path:{data_path}")

    game_data_path = args.game_data_path or bases.get_value("game_data_path")
    print(f"param\t game_data_path:{game_data_path}")

    char_info, char_name_info = get_all_char_info(game_data_path)

    char_missing = []
    for char_name, val in char_name_info.items():
        char_id = val["charId"]
        char_sum_path = os.path.join(data_path, "char_v3", f"{char_id}.txt")
        if os.path.exists(char_sum_path):
            continue
        char_sum_path = os.path.join(data_path, "chars", f"{char_id}.txt")
        if os.path.exists(char_sum_path):
            continue
        char_missing.append(char_name)
        print(char_name, char_sum_path)
    print(char_missing)

import os
from libs import bases
from libs.game_data import (
    extract_data_from_story_review_table,
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

    story_review_data = extract_data_from_story_review_table(game_data_path)

    event_missing = []
    for event_id, val in story_review_data.items():
        event_sum_path = os.path.join(data_path, "stories", f"{event_id}.txt")
        if os.path.exists(event_sum_path):
            continue
        event_missing.append(event_id)
    print(event_missing)

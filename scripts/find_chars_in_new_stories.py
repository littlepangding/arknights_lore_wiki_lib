"""Step 2.5 of the update flow.

Reads new story summary files (data/stories/<id>.txt), extracts <关键人物>
from each, resolves names through char_alias.txt, and partitions the
result into:

  - existing canonical chars whose wiki page already exists
    (-> need re-run of get_char_wiki_v3 to pick up the new event)
  - new chars with no wiki page yet
  - unresolved aliases (a name not found in alias file but similar to an
    existing canonical) -- surfaced for manual review of char_alias.txt

Output is human-readable; the candidate char list MUST be reviewed by
the user before feeding into get_char_wiki_v3 (LLM 关键人物 extraction
is noisy and may include irrelevant chars).

Usage:
    python -m scripts.find_chars_in_new_stories \
        --new-stories tmp/stories_<date>.txt \
        [--out tmp/char_<date>.txt]
"""

import argparse
import os
import sys

from libs import bases
from libs.bases import extract_tagged_contents, get_txt_files
from libs.game_data import get_all_char_info, get_char_file_name


def parse_alias_file(path):
    """Returns (canonical_set, alias_to_canonical dict)."""
    if not os.path.exists(path):
        return set(), {}
    with open(path, "r") as f:
        txt = f.read()
    canonical = set()
    alias_to_canonical = {}
    for line in txt.splitlines():
        names = [v.strip() for v in line.split(";") if v.strip()]
        if not names:
            continue
        canon = names[0]
        canonical.add(canon)
        for n in names:
            alias_to_canonical[n] = canon
    return canonical, alias_to_canonical


def existing_wiki_chars(data_path):
    """Set of (canonical_name_or_charid) that already have a wiki page.

    We can't recover canonical names from char_v3 filenames alone for
    `extended_char_*` entries, so we return both the file_name set and
    the inferred ID set.
    """
    out = set()
    for sub in ("char_v3", "chars"):
        d = os.path.join(data_path, sub)
        if not os.path.isdir(d):
            continue
        for f in get_txt_files(d):
            if f.startswith("prompt_") or f.startswith("depre"):
                continue
            out.add(f[:-4])
    return out


def extract_key_chars_from_story(story_file):
    with open(story_file, "r") as f:
        txt = f.read()
    found = extract_tagged_contents(txt, "关键人物")
    if not found:
        return []
    raw = found[0]
    # 关键人物 is `;` separated per the prompt; tolerate `；` too
    parts = []
    for chunk in raw.replace("；", ";").split(";"):
        n = chunk.strip()
        if n:
            parts.append(n)
    return parts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--new-stories",
        required=True,
        help="path to a file with one story_id per line (typically tmp/stories_<date>.txt)",
    )
    parser.add_argument("--wiki-path", default="")
    parser.add_argument("--game-data-path", default="")
    parser.add_argument(
        "--out",
        default=None,
        help="if given, write candidate names here (one per line)",
    )
    args = parser.parse_args()

    wiki_path = args.wiki_path or bases.get_value("lore_wiki_path")
    data_path = os.path.join(wiki_path, "data")
    game_data_path = args.game_data_path or bases.get_value("game_data_path")
    alias_path = os.path.join(data_path, "char_alias.txt")
    print(f"param\t wiki_path:{wiki_path}")
    print(f"param\t alias_path:{alias_path}")

    with open(args.new_stories, "r") as f:
        story_ids = [l.strip() for l in f if l.strip()]
    print(f"reading {len(story_ids)} new stories from {args.new_stories}")

    canonical_set, alias_to_canon = parse_alias_file(alias_path)
    print(f"alias file: {len(canonical_set)} canonical names, "
          f"{len(alias_to_canon)} total aliases")

    existing_pages = existing_wiki_chars(data_path)
    char_info, char_name_info = get_all_char_info(game_data_path)
    playable_names = set(char_name_info.keys())
    name_to_charid = {n: char_name_info[n]["charId"] for n in playable_names}

    # Aggregate raw names -> set of stories where they appear
    name_to_stories = {}
    missing_files = []
    for sid in story_ids:
        # locate the story summary file: data/stories/<id>.txt
        path = os.path.join(data_path, "stories", sid + ".txt")
        if not os.path.exists(path):
            missing_files.append(sid)
            continue
        for n in extract_key_chars_from_story(path):
            name_to_stories.setdefault(n, set()).add(sid)

    if missing_files:
        print(
            f"WARNING: {len(missing_files)} story summary files missing "
            f"(run get_story_wiki first): {missing_files}",
            file=sys.stderr,
        )

    # Resolve every raw name through alias file
    existing_canonical = []  # canonical chars whose page exists -> needs re-run
    new_candidates = []  # new chars (no page yet)
    unresolved = []  # name not in alias and not in playable -> review needed

    seen_canonical = set()
    for raw, stories in sorted(name_to_stories.items()):
        canon = alias_to_canon.get(raw, raw)
        # If canon is a playable name, the page lives at char_v3/<charId>.txt
        page_id = name_to_charid.get(canon)
        if page_id and page_id in existing_pages:
            if canon not in seen_canonical:
                existing_canonical.append((canon, page_id, sorted(stories)))
                seen_canonical.add(canon)
            continue
        # Non-playable: the wiki page lives at extended_char_* if it exists.
        page_id = get_char_file_name(canon, char_name_info)
        if page_id in existing_pages:
            if canon not in seen_canonical:
                existing_canonical.append((canon, page_id, sorted(stories)))
                seen_canonical.add(canon)
            continue
        if canon in seen_canonical:
            continue
        seen_canonical.add(canon)
        if raw == canon:
            # canonical (or no alias entry); is it new or existing-extended?
            new_candidates.append((canon, None, sorted(stories)))
        else:
            # we resolved an alias to a canonical that has no page
            new_candidates.append((canon, None, sorted(stories)))
        # flag unresolved aliases (raw not in alias file, not playable, but
        # similar to a known canonical) -- naive: substring check
        if raw not in alias_to_canon and raw not in playable_names:
            for k_canon in canonical_set:
                if (raw != k_canon and (raw in k_canon or k_canon in raw)
                        and len(raw) >= 2 and len(k_canon) >= 2):
                    unresolved.append((raw, k_canon, sorted(stories)))
                    break

    print()
    print("=" * 60)
    print(f"EXISTING chars mentioned in new stories ({len(existing_canonical)}):")
    print("(re-run get_char_wiki_v3 with these to add the new event summary)")
    for canon, page_id, stories in existing_canonical:
        print(f"  {canon}\t({page_id})\t{','.join(stories)}")

    print()
    print(f"NEW char candidates ({len(new_candidates)}):")
    print("(REVIEW manually — LLM extraction is noisy; prune unimportant chars)")
    for canon, _, stories in new_candidates:
        print(f"  {canon}\t{','.join(stories)}")

    if unresolved:
        print()
        print(f"POTENTIAL ALIASES to add to char_alias.txt ({len(unresolved)}):")
        for raw, similar, stories in unresolved:
            print(f"  {raw!r} looks similar to canonical {similar!r}\t{','.join(stories)}")

    if args.out:
        with open(args.out, "w") as f:
            for canon, _, _ in existing_canonical:
                f.write(canon + "\n")
            for canon, _, _ in new_candidates:
                f.write(canon + "\n")
        print(f"\nwrote {len(existing_canonical) + len(new_candidates)} candidates to {args.out}")
        print("NOTE: review the file and remove unimportant chars before running the char batch")


if __name__ == "__main__":
    main()

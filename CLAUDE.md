# Claude Code Guide — arknights_lore_wiki_lib

This file briefs Claude Code on the lib repo so future sessions don't relearn the layout. Keep it terse and current.

## Three-repo architecture

The full pipeline lives across three sibling repos under `~/Claude/arknights/`:

| Repo | Role | Branch | Touched by code? |
|---|---|---|---|
| `ArknightsGameData/` | Upstream game data dump (`zh_CN/gamedata/`) | `master` | read-only |
| `arknights_lore_wiki_lib/` | This repo. All processing scripts. | feature branches off `main` | n/a |
| `arknights_lore_wiki/` | Published wiki (data + docs) | work on `update`, PR to `main` | written by `compile_website.py` and the LLM scripts |

Paths are wired via `keys.json` (`game_data_path`, `lore_wiki_path`). `keys.json` is gitignored — copy from a teammate or recreate it.

## Running anything

```
cd ~/Claude/arknights/arknights_lore_wiki_lib
.venv/bin/python -m scripts.<name> ...
```

On a fresh checkout: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. System python on macOS will fail with PEP 668 — always use the venv.

## End-to-end update flow

When the upstream `ArknightsGameData` updates, the user runs the update flow. **Don't piece it together by hand — invoke the skill `update-lore-wiki` (`.claude/skills/update-lore-wiki/SKILL.md`).** It enforces the human gates (story list, candidate char list) and the validation passes that the user requires before any LLM call.

High-level: find new stories → confirm with user → LLM-summarize each → extract candidate chars from summaries → confirm with user (alias-resolve, prune) → LLM-generate per-char wiki → compile site → commit & PR.

## For knowledge-base / Q&A / audit work

A separate, in-progress effort builds an agent-facing knowledge base over the raw game data (lore Q&A, audit new wiki updates, audit existing pages). When a task involves any of those, **start with `docs/README.md`** — it indexes:

- `docs/REQUIREMENTS.md` — user intent for the KB.
- `docs/DESIGN.md` — architecture: `libs/kb/`, `data/kb/` (gitignored raw chunks), `kb_summaries/` (committed LLM aids), four source families, deterministic vs inferred char↔event edges.
- `docs/AGENTS_GUIDE.md` — how to use the KB CLIs (`kb_query`, `kb_audit_wiki`, `kb_summarize`).
- `docs/PROMPTS.md` — Chinese prompt templates for summary / audit flows.
- `docs/DECISIONS.md` — substantial-decision log.
- `docs/reviews/` — review feedback applied to the design.

The KB layer is **independent** of the wiki update flow above. They share `libs/game_data.py` and the LLM backends but the directories don't overlap.

## Layout

```
libs/
  bases.py        # tag schema, validation, LLM dispatch (cli/gai), retry, file utils
  game_data.py    # parse ArknightsGameData JSON; clean raw story scripts
  ui.py           # render data/*.txt -> docs/*.md; build indexes & cross-links
scripts/
  find_new_stories.py             # diff story_review_table vs data/stories/
  find_chars_in_new_stories.py    # extract <关键人物> from new stories, alias-resolve
  find_new_chars.py               # legacy: scan game data for chars without a wiki page
  get_story_wiki.py               # LLM: 1 event -> data/stories/<id>.txt
  get_char_wiki_v3.py             # LLM (3-step, checkpointed): 1 char -> data/char_v3/<file>.txt
  compile_website.py              # render docs/, build indexes, emit README "what's new" snippet
.claude/skills/update-lore-wiki/  # the orchestrating skill
```

## Data flow & key game-data files

`libs/game_data.py` reads from `<game_data_path>/zh_CN/gamedata/`:

- `excel/story_review_table.json` — the master event index. Every `id` is a story_id (e.g. `act46side`, `main_16`). Each event has `infoUnlockDatas` listing per-stage entries with `storyName`, `storyTxt` (path to script), `avgTag` (`行动前`/`行动后`/`幕间`).
- `story/activities/<id>/`, `story/obt/main/` etc. — raw scripts referenced by `storyTxt`. Cleaned by `clean_script` in `game_data.py`.
- `excel/character_table.json`, `charword_table.json`, `handbook_info_table.json`, `skin_table.json`, `uniequip_table.json` — char metadata. Combined into `char_info`/`char_name_info` via `get_all_char_info`.

## Wiki outputs (in `arknights_lore_wiki/`)

- `data/stories/<id>.txt` — LLM story summary, validated against `story_wiki_tags`.
- `data/char_v3/<file_name>.txt` — LLM char wiki, validated against `char_wiki_tags`.
  - Playable: `<file_name>` is `charId` (e.g. `char_002_amiya`).
  - Non-playable: `extended_char_<lazy_pinyin_or_6char_sha>` from `get_char_file_name`.
- `data/char_v3/prompt_<file_name>.txt` — cached final-step prompt (resume helper). Treat as build cache.
- `data/chars/...` — legacy v1 char wikis, still rendered by `compile_website`.
- `data/char_alias.txt` — `canonical;alias1;alias2;...` per line, **canonical name always first**. Used by `find_chars_in_new_stories.py` to resolve LLM-extracted names.
- `docs/<sub>/*.md` — generated; never hand-edit.
- `README.md` — gets a "what's new" section appended each update from `compile_website.py`'s emitted snippet.

## LLM concerns

- **Backend** in `keys.json`: `llm_backend` = `"cli"` (default `gemini-3.1-flash`, shells out to `gemini` binary) or `"gai"` (`google.genai` SDK, default `gemini-2.5-flash`). Override per-script with `--llm` / `--model`.
- **Validation**: the generator scripts validate model-owned tags before writing `data/stories/*.txt` and `data/char_v3/*.txt`. `query_llm_validated` retries once with an explicit reminder; persistent failures raise `LLMError`. Don't silently skip — surface to the user.
- **Cost**: existing chars with N events already cached only re-summarize *new* events in `get_char_wiki_v3.py` step 2. Don't pass `--force` unless asked — it nukes `tmp/char_v3_cache/<file>/` and re-bills every event.
- **Resume**: step 2 of `get_char_wiki_v3.py` checkpoints each event to `tmp/char_v3_cache/<file>/<event_id>.txt` immediately. Re-running the same command resumes mid-batch. Step 3 (synthesis) cache lives at `data/char_v3/prompt_<file>.txt`.

## Conventions / gotchas

- All commands are run from the lib repo root (cwd). Several modules read `keys.json` via a relative path — running from elsewhere will fail.
- The wiki repo's `update` branch is the working branch; `main` is the publish target. Always commit story/char updates to `update`.
- `get_simple_filename` falls back to a 6-char sha hash when the input has non-`[a-zA-Z0-9_.]` chars. Used both for non-playable char filenames and as a story-id sanitizer.
- File writes for cached LLM outputs use `os.replace` for atomicity; treat that as load-bearing if you refactor.

## Recently Fixed Landmines

When working on `libs/game_data.py` or auditing existing wiki content, be aware:

- **`clean_script` used to drop `Sticker` text.** The catch-all pure-bracket strip removed `[Sticker(...text="…")]` lines before this branch extracted them. Existing wiki outputs written before commit `8dfb2f7` may therefore miss meaningful narration / inscriptions / scripture, including lore-heavy passages such as the act46side opening.
- **Chapter names used to collide inside an event.** `get_all_text_from_event` previously emitted `<章节名称>{storyName}</章节名称>` and ignored `avgTag`, so `_beg` and `_end` chapters with the same `storyName` were indistinguishable to the LLM. This branch appends `（avgTag）`, but older summaries were generated before that fix.
- **`storySort` used to be ignored.** Chapter order previously relied on raw JSON ordering instead of `storySort`. This branch sorts stages defensively, but older summaries may still reflect the pre-fix ordering.

## Status of past LLM outputs

LLM summaries written before the bug fixes above were generated from incomplete cleaned text (Sticker dropped, ambiguous chapter names). They are not necessarily wrong, but key narration may be missing. Treat re-runs against fixed text as a real upgrade, not a no-op — but they cost tokens.

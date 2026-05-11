# arknights_lore_wiki_lib

Scripts that turn the upstream `ArknightsGameData` JSON dump into:
1. A published lore wiki (story summaries, character pages) at [`arknights_lore_wiki`](https://github.com/littlepangding/arknights_lore_wiki).
2. An agent-readable knowledge base on top of the raw game data (Q/A, audits).

LLM work is offloaded to the Gemini CLI / Gemini SDK / Claude CLI so the orchestrating agent doesn't burn its own context window.

## How the three repos fit together

```
ArknightsGameData/        (upstream, read-only — pulled from the game data project)
        │
        ▼  parsed by libs/game_data.py
this repo (arknights_lore_wiki_lib/)
        │
        ▼  written by scripts/compile_website.py + the LLM scripts
arknights_lore_wiki/      (the published wiki: data/*.txt + docs/*.md)
```

Paths to the other two are wired via `keys.json` (gitignored — see Setup).

## Setup

```bash
git clone git@github.com:littlepangding/arknights_lore_wiki_lib.git
cd arknights_lore_wiki_lib
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `keys.json` next to this README:

```json
{
  "lore_wiki_path": "/abs/path/to/arknights_lore_wiki",
  "game_data_path": "/abs/path/to/ArknightsGameData",
  "llm_backend": "cli",
  "llm_model": "gemini-3-flash-preview",
  "genai_api_key": "..."
}
```

Run everything from the repo root with the venv (`keys.json` is loaded by relative path):

```bash
.venv/bin/python -m scripts.<name>
```

## What you can do

### 1. Update the lore wiki when game data updates

After pulling new content into `ArknightsGameData/`, the end-to-end flow is:

> find new stories → confirm with user → LLM-summarize each → extract candidate chars → confirm → LLM-generate per-char wiki → compile site → commit & PR.

Don't piece it together by hand. Invoke the orchestrating skill from Claude Code:

```
/update-lore-wiki
```

The skill enforces the human review gates and validation passes that the user requires before any LLM call. See `.claude/skills/update-lore-wiki/SKILL.md`.

### 2. Build the knowledge base (no LLM)

```bash
.venv/bin/python -m scripts.kb_build
```

Reads `ArknightsGameData/`, writes deterministic per-stage and per-character chunks under `data/kb/` (gitignored), plus a handful of JSON indexes. Runs in ~8s against the live corpus. Idempotent; default-on prune.

### 3. Query the knowledge base

The KB is designed for command-line consumption by an agent or a human:

```bash
.venv/bin/python -m scripts.kb_query event list --family activity
.venv/bin/python -m scripts.kb_query event get act46side
.venv/bin/python -m scripts.kb_query event chars act46side --source deterministic
.venv/bin/python -m scripts.kb_query char resolve 阿米娅
.venv/bin/python -m scripts.kb_query char get char_002_amiya --section voice
.venv/bin/python -m scripts.kb_query grep 巨枭
```

JSON output by default; `--text` returns raw chunks where applicable.

### 4. Bake LLM event summaries (`kb_summaries/`)

Optional layer — small zh summaries committed to git as a navigation aid.

```bash
.venv/bin/python -m scripts.kb_summarize --event story_12fce_set_1   # one event
.venv/bin/python -m scripts.kb_summarize                              # all events (token cost)
.venv/bin/python -m scripts.kb_summarize --llm claude                 # use Claude CLI
.venv/bin/python -m scripts.kb_summarize --estimate                   # dry-run: how many events / LLM calls / ~tokens are left
```

Source-hash cache: re-runs over unchanged events are no-ops (no token re-spend). A real run streams a per-event progress line (`[i/N] <event_id>  done X/Y ev  ~tok_done/tok_total  elapsed  ETA ~…`) so a multi-hour bake isn't silent. `--estimate` calls no LLM — it just prints the projected cost (events to run, single vs multi pass, LLM calls, input/output/total chars ≈ tokens) of the run that *would* happen; honors `--event` / `--force`.

## Skills (Claude Code)

Repo-local skills under `.claude/skills/` orchestrate the multi-step flows so you don't have to piece them together by hand. Invoke from a Claude Code session by mentioning the skill name (or just describing the task — the trigger phrases in each `SKILL.md` will match):

| Skill | What it does |
|---|---|
| `update-lore-wiki` | The end-to-end wiki update flow after game data lands: find new stories → confirm → LLM-summarize → discover candidate chars → confirm → LLM-generate char wikis → compile → PR. Enforces the human review gates before any LLM call. |
| `refresh-kb` | Bring `data/kb/` (deterministic build) and `kb_summaries/` (LLM event summaries) up to date. Same flow handles first-time full builds and later incremental refreshes; `kb_build` is idempotent and `kb_summarize` is hash-skip cached, so running it freely costs nothing for unchanged content. |

Each skill lives in its own folder under `.claude/skills/`. Read the `SKILL.md` to see the exact preconditions, workflow, and trigger phrases.

## Layout

```
libs/
  bases.py        # tag schema, extract_tagged_contents, validation, get_value
  game_data.py    # parse ArknightsGameData JSON; clean_script
  llm_clients.py  # unified Gemini CLI / SDK / Claude CLI dispatch
  ui.py           # render data/*.txt → docs/*.md for the published wiki
  kb/             # the knowledge-base package (paths, chunker, indexer, query, summarize)
scripts/
  find_new_stories.py + find_chars_in_new_stories.py  # update-flow helpers
  get_story_wiki.py + get_char_wiki_v3.py             # LLM generators
  compile_website.py                                   # render wiki docs
  kb_build.py + kb_query.py + kb_summarize.py         # KB CLIs
tests/             # pytest, mock-only — no real LLM calls in CI
docs/              # design history (REQUIREMENTS, DESIGN, PROMPTS, DECISIONS, reviews/)
```

## Going deeper

- **Implementation/reviewer:** read `docs/DESIGN.md` (architecture + risks) and `docs/DECISIONS.md` (substantial-decision log).
- **Agent using the KB for Q/A or audits:** read `docs/AGENTS_GUIDE.md`.
- **Modifying a prompt:** read `docs/PROMPTS.md`.
- **AI assistant orientation:** the per-folder `CLAUDE.md` files are a terser-and-current operational briefing.

## Conventions

- All commands run from the repo root.
- Always use `.venv/bin/python` — system Python on macOS hits PEP 668.
- `keys.json` is gitignored and contains an API key. Never commit it.
- Each generated file write uses an atomic temp + `os.replace` so a partial write never lands at the destination — load-bearing if you refactor.

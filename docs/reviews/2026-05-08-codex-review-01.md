# Codex Review 01 — KB Design Draft

Date: 2026-05-08

Scope reviewed:
- `docs/REQUIREMENTS.md`
- `docs/DESIGN.md`
- `docs/AGENTS_GUIDE.md`
- `docs/PROMPTS.md`
- `.agents/skills/audit-lore-wiki/SKILL.md`
- current repo state under `scripts/`, `libs/`, `AGENTS.md`

Workspace snapshot at review time:
- `git status --short` showed new untracked `docs/` and `.agents/skills/audit-lore-wiki/`
- no `scripts/kb_*.py` or `scripts/audit_*.py` modules exist yet

## Findings

### 1. Blocking: the new `audit-lore-wiki` skill is "live" but points to code that does not exist

The skill reads like an executable workflow, not a design stub. It tells future agents to run:

- `scripts.audit_all`
- `scripts.audit_tags`
- `scripts.audit_grounding`
- `scripts.audit_sticker_loss`

See `.agents/skills/audit-lore-wiki/SKILL.md:8-33`, `:51-63`, `:69-109`.

Those modules are not present in `scripts/` today; the repo currently contains only:

- `compile_website.py`
- `find_chars_in_new_stories.py`
- `find_new_chars.py`
- `find_new_stories.py`
- `get_char_wiki_v3.py`
- `get_story_wiki.py`

This is risky because a future agent can now legitimately trigger `audit-lore-wiki` and immediately fail on nonexistent entry points. If the goal of this phase is "design and minor stuff, do not start implementing", then this skill should either:

- be removed until the scripts exist, or
- be marked explicitly as proposal-only and moved into `docs/` instead of `.agents/skills/`

### 2. Important: the "progressive disclosure from repo root" requirement is not satisfied yet

The user requirement is explicit: an agent starting at `arknights_lore_wiki_lib/` root should be able to discover the KB flow through `CLAUDE.md` / `AGENTS.md` without preloading everything. See `docs/REQUIREMENTS.md:13-14`.

`docs/DESIGN.md` also promises a top-level pointer section in `CLAUDE.md` for KB / audit work. See `docs/DESIGN.md:294-312`.

That pointer has not been added. The current root `AGENTS.md` still only documents the existing wiki-generation pipeline and does not mention:

- `docs/README.md`
- `docs/AGENTS_GUIDE.md`
- KB work
- audit work

See `AGENTS.md:26-47` and `AGENTS.md:57-67`.

The new docs are good raw material, but without a root entry point they do not yet deliver the discovery behavior the user asked for.

### 3. Important: the design says "raw game data only", but the planned index build depends on wiki-side alias data

`docs/REQUIREMENTS.md` records a settled scope decision:

- `KB scope | Raw game data only. Existing LLM-generated wiki summaries (...) are an audit target, not a query layer.`

See `docs/REQUIREMENTS.md:39-42`.

But `docs/DESIGN.md` proposes reading `arknights_lore_wiki/data/char_alias.txt` into `char_alias.json` during KB indexing. See `docs/DESIGN.md:179-186`.

That creates a hidden dependency on the sibling wiki repo and weakens two desirable properties:

1. The KB is no longer buildable from only `ArknightsGameData` plus lib code.
2. The audit/query layer inherits curated state from the audit target repo.

This may still be an acceptable product choice, but it should be called out as an explicit scope change rather than framed as still being "raw game data only".

Possible ways to resolve it:

- move alias metadata into the lib repo as first-class tracked input, or
- make wiki aliases an optional enrichment step layered on top of the raw KB, or
- revise the requirements doc to say the KB depends on raw game data plus curated alias metadata

### 4. Medium: several "verified" chunk-size and timing claims are not yet backed by a reproducible script

The design and agent guide assert measurements such as:

- stage chunk median / p95 / max sizes
- build time under minutes
- grounding audit runtime around 30 seconds

See `docs/DESIGN.md:139`, `:242-251`, `:285-287`, `:365-371`, and `.agents/skills/audit-lore-wiki/SKILL.md:132-133`.

Given the user's "use code and unit tests whenever possible" instruction, these should ideally come from:

- a tiny measurement script checked into `scripts/` or `tmp/`
- or at least a note naming the command used to derive the numbers

Otherwise the plan bakes in thresholds like `200 KB` and "median 5K" without a reproducible paper trail, which makes later review harder.

## Strong parts

- `docs/REQUIREMENTS.md` is a solid capture of the user's intent and preserves the important constraints clearly.
- Splitting raw gitignored chunks from small committed summary aids is directionally good and matches the user's copyright concern.
- Writing the LLM prompts in Chinese and keeping them reviewable in `docs/PROMPTS.md` is a strong call.
- Reusing `libs/game_data.py` and existing LLM-dispatch patterns is the right starting posture instead of inventing a parallel stack.

## Suggested next steps before implementation

1. Remove or demote the `audit-lore-wiki` skill until real entry points exist.
2. Add a minimal root-level routing section in `AGENTS.md` and `CLAUDE.md` that points KB / Q&A / audit tasks to `docs/README.md` and `docs/AGENTS_GUIDE.md`.
3. Decide whether alias data is part of the KB input contract. If yes, document it explicitly. If no, move it out of the base build path.
4. Add one deterministic measurement command or script for chunk sizes before locking in prompt-size thresholds.

## Review status

This design is promising, but I would not treat it as ready-for-implementation until Findings 1-3 are addressed. Finding 4 is not blocking, but it is worth tightening before chunk-size assumptions spread into code and prompts.

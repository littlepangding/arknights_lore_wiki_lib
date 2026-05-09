# `docs/` — Design & Working History for the Lib Repo

This folder is the agent working area: requirements, design docs, prompts, decision history. It's tracked in git so future sessions and reviewers always have the same context.

## Files

| File | Purpose | Read when |
|---|---|---|
| `REQUIREMENTS.md` | The user's stated requirements for the agent-readable knowledge base. | You're orienting on *why* the KB exists. |
| `DESIGN.md` | The architecture + module layout + risks. | You're about to implement, review, or refactor any KB component. |
| `PROMPTS.md` | All Chinese prompts used by `summarize` and `audit` flows, with required output tags. | You're tweaking a prompt, or debugging an LLM-output validation failure. |
| `AGENTS_GUIDE.md` | How an agent should *use* the KB (CLI surface + recipes). | You've been asked to do Q/A or audit work. |
| `DECISIONS.md` | Substantial-decision log. Append-only. | You're tracing why something is built the way it is. |

## Where the rest of the project's docs are

- Top-level `CLAUDE.md` / `AGENTS.md` (lib repo root): pipeline + 3-repo overview + update flow.
- `.claude/skills/update-lore-wiki/SKILL.md`, `.agents/skills/update-lore-wiki/SKILL.md`: the update-flow skill.
- `arknights_lore_wiki/CLAUDE.md`: a one-page note saying "everything here is generated".

## Status

| Component | State |
|---|---|
| Requirements + design docs | ✅ written + revised across Codex reviews 01-07. |
| Codex reviews | ✅ in `reviews/` (8 files: 01, 02, 03, 04, 05, 06, 07-consistency, 07-independent). All findings folded into DESIGN.md / AGENTS_GUIDE.md / PROMPTS.md. |
| Prompt drafts | ✅ written, **not yet run against any model**. |
| `libs/kb/` package | 🟡 partial — Phases 1-2 landed: `paths.py`, `chunker.py`, `indexer.py`, `query.py`. Pending: `summarize.py`, `llm_clients.py`. |
| `tests/` | 🟡 partial — `tests/conftest.py`, `test_paths.py`, `test_chunker.py`, `test_indexer.py`, `test_query.py`, `tests/fixtures/mini_gamedata/`. **127 passing.** Pending: `test_llm_clients.py`. |
| `scripts/kb_*.py` | ❌ not implemented (Phase 3+). |
| `data/kb/` raw chunks | ❌ not built. |
| `kb_summaries/` (in git) | ❌ folder doesn't exist yet (Phase 5). |

Implementation phases are listed in `DESIGN.md#implementation-phases-proposed`.

## Reviews to date

- `reviews/2026-05-08-codex-review-01.md` — flagged the alias-data scope contradiction (fixed: now optional enrichment), missing root pointer (fixed: added to `CLAUDE.md` / `AGENTS.md`), unverified measurements (fixed: new Measurements section), and a pre-existing broken `audit-lore-wiki` skill (resolved: moved to `proposals/`).
- `reviews/2026-05-08-codex-review-02-kb-structure.md` — corpus-shape critique. Folded in: source-family axis, sectional char layout, deterministic vs inferred edges, `storyTxt`-prefix metadata.
- `reviews/2026-05-08-codex-review-03-updated-doc-alias.md` — alias-handling honesty pass. Folded in: explicit operator-only v1 scope, `appellation` parser extension, `resolve_operator_name` with tagged-union return, alias-coverage realism in docs.
- `reviews/2026-05-08-codex-review-04-simplicity-cost-correctness.md` — simplicity / cost / correctness pass. Folded in: M4 measurement bug fixed, M5 added, multi-pass summary trigger lowered to 80K-or-stage_count>10, per-char summaries dropped from v1, stage-precise deterministic edges restored with new `stage_chars` query.
- `reviews/2026-05-08-codex-review-05-followup.md` — tighten-the-contract pass. Folded in: build rule narrowed to "has name" (5 nameless `npc_*` skipped), PROMPTS.md threshold synced, `grep_text` literal-by-default with `--regex` opt-in, audit budget caps baked in.
- `reviews/2026-05-08-codex-review-06-independent.md` — independent pass against `REQUIREMENTS.md`. Folded in: inferred-edge matcher split into three classes (no floor for canonical operator names — restores 23 single-char operators), two-signal audit (entity-diff + claim-level LLM verdicts), ambiguous-canonical curated aliases now surfaced as `Ambiguous` rather than auto-attached.
- `reviews/2026-05-08-codex-review-07-consistency.md` — internal consistency pass. Folded in: P3 prompt split into P3a/P3b matching the two-signal contract, `Appearance` and `event_to_chars.json` flattened to one row-per-stage shape, prune rule added to build contract, small inline drift (README "01 & 02" + DESIGN.md ASCII shape) corrected.
- `reviews/2026-05-08-codex-review-07-independent.md` — independent pass against `REQUIREMENTS.md`. Folded in: Signal 1 broadened with NPC-shaped candidate extraction (closes NPC-omission blind spot), `match_class` threaded through `Appearance` + CLI output, AGENTS_GUIDE Q/A example rewritten (`司辰` was unresolvable; replaced with `陈` and `特蕾西娅` flows).

## Subfolders

- `reviews/` — reviewer feedback applied to the design.
- `proposals/` — archived design specs that are not live skills (e.g. `audit-lore-wiki-prior-spec.md`, the orphan audit framework). These inform later phases but are not invokable.

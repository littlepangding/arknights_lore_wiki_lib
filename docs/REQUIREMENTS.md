# Knowledge-Base Requirements

> Captured from a planning conversation on 2026-05-08. The user's voice is preserved as faithfully as possible. Subsequent design (`DESIGN.md`) is a response to *these* requirements; if a design choice contradicts something here, that contradiction should be visible and justified, not silent.

## Stated goals

1. **Build a code-driven, agent-readable knowledge base** by parsing `ArknightsGameData` into chunks that an LLM agent can read and search efficiently.
   - Reuse and improve upon what already exists in `arknights_lore_wiki_lib/` rather than replace it.
   - The KB should be **constructible without any LLM call** — pure code.
1. **Two-tier persistence:**
   - Raw parsed chunks and indexes are **gitignored** (copyright-sensitive).
   - LLM-processed summaries / index aids **may be committed** (less copyright-risky because processed/derivative, and useful as navigation context across sessions).
1. **Progressive disclosure for agents.** An agent dropped into `arknights_lore_wiki_lib/` root should be able to find its way through CLAUDE.md / AGENTS.md to whatever sub-doc it needs without preloading everything.
1. **Reasonable chunk sizes.** Each raw-text chunk should fit in an LLM context window comfortably so that multiple chunks can be assembled into a single prompt without trouble.

## Target applications

1. **Ad-hoc Q/A** about Arknights lore (an agent answering a user's question by retrieving relevant chunks).
2. **Auditing a new wiki update** — when a fresh batch of summaries is generated, cross-check them against the raw text in the KB to flag missing scenes / wrong attributions / hallucinations.
3. **Auditing existing wiki pages** — same mechanic, applied retroactively to already-published pages.

## Working principles the user emphasized

1. **Prefer code with unit tests over agent reasoning.** Every step that *can* be deterministic should be deterministic and testable. Agent reasoning is reserved for tasks that genuinely require it.
2. **Design multiple LLM access points.** Summarization work should be offloadable to:
   - **Gemini CLI** (`gemini -m ... -p ...`) — default, already wired into `bases.py`.
   - **Gemini API** (`google.genai` SDK) — already wired.
   - **Claude CLI** (`claude -p ...`) — new, to be added.
   The agent's own context window is reserved for code/prompt writing, not bulk summarization.
3. **Write LLM prompts in Chinese.** The source material is Chinese; prompts and outputs should match to keep the model in-language.
4. **Document everything.** Requirements, design, and decisions live in `arknights_lore_wiki_lib/docs/`, **committed to git**, so that future agent sessions (and human reviewers) have full historical context. The design doc is reviewable by another agent before any implementation starts.

## Decisions made during the planning conversation

These were settled via clarifying questions, before this doc was written:

| Question | Decision |
|---|---|
| KB location on disk | `arknights_lore_wiki_lib/data/kb/` (gitignored raw + index) and `arknights_lore_wiki_lib/kb_summaries/` (in git: LLM-made summaries). |
| KB scope | **Raw game data only** for the build contract. Existing LLM-generated wiki summaries (`arknights_lore_wiki/data/`) are an *audit target*, not a query layer. **Hand-curated alias metadata** (`arknights_lore_wiki/data/char_alias.txt`) is treated as **optional enrichment** — the KB builds without it; if present it improves char-name grep recall. (Clarified post-review-01-finding-3.) |
| LLM backends to support | All three: Gemini CLI (default), Gemini API, Claude CLI. User explicitly added "gemini cli as default". |
| Retrieval mechanism | Structured indexes + grep. No embeddings in v1. |

## Out of scope (v1)

- Embeddings / vector search.
- Web UI for the KB. Agent CLI access is sufficient.
- Translating any of the content. KB stays in zh-CN.
- Re-indexing of the published wiki summaries. They are read-only audit targets.
- Automatic CI on git commits. Manual builds only for now.

## Success criteria

- An agent dropped into `arknights_lore_wiki_lib/` (with KB built and `keys.json` present) can answer a lore question like *"司辰在哪些活动里出现过？"* by reading the KB without preloading the entire game data.
- An agent can take a freshly generated story summary and produce a list of "scenes mentioned in raw text but missing from summary" by comparing chunks.
- All of the above is reproducible from a fresh checkout in `<10 min` of building (no LLM calls), or `<2 hr` if including the optional LLM-summary bake.

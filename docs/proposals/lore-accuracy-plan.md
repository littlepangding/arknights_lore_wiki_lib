# Plan: improving lore search & validation accuracy

> Status: proposal (2026-05-12). Driven by a concrete failure (assistant stitched "黍 is the cook 弟弟 年 mentioned" from two unrelated facts, contradicting 黍's own 性别/排行) plus a manual audit of `arknights_lore_wiki/data/char_v3/char_2025_shu.txt`. Token cost is acknowledged but secondary to correctness: retrieval should reach raw chapter text, guided per-`<章节>`, not per-event-summary.

## 1. Test cases (these define "correct")

**TC-1 — Hallucinated relation (黍 / 弟弟).** "黍 是年口中那个会做饭的弟弟" is false: 年's voice line names an *unnamed* 弟弟 (male); 黍 is 女 and is a *姐姐* to 年. Acceptance:
- Q&A: "年口中会做饭的弟弟是谁?" → "未具名 — 年语音提到一个会做饭的弟弟（cite），无任何文本指认其身份；黍为女性、年的姐姐（cite），排除。"
- Validation: a page asserting this claim is flagged with the missing-link evidence.

**TC-2 — Gap-aware enumeration (十二岁兽).** Acceptance: "列出年家十二姐妹" → *named* set with char_id + cite each, *hinted-but-unnamed* set with cite each, and a *not-yet-revealed* count. Never padded to 12 with guesses. (From the wiki today the named-ish set is already ~重岳/望/绩/黍/年/夕/令/颉 — and several of their *排行* numbers are uncited; the answer must say so.)

**TC-3 — Char-page claim tracing.** Acceptance: given `char_v3/<file>.txt`, emit (i) structural/xref/coverage/card/relation flags from code, (ii) a per-claim `有依据/无依据/不确定` table where every "有依据" carries a *verbatim* quote from a *raw `<章节>` chunk* (never from a story summary), code-rechecked.

## 2. What the manual audit of `char_2025_shu.txt` already shows (gaps in current tooling)

Running passes a/b/c by hand against the raw KB:

- **Unresolved `<相关角色>`**: `绩`, `颉`, `神农` don't `char resolve` (NPCs / mentioned-only); `禾生` resolves to `char_4119_wanqin` — needs a human check (alias correct, or mis-resolution?). → an `xref` pass catches all of these *today* with `kb_query char resolve`; we just haven't built it.
- **Uncited structured claims**: the page asserts a full Sui birth-order — 重岳=长子, 望=第二, 绩=第三, 黍=第六, 年=第九, 夕=第十一. 黍=第六 is grounded (`档案资料一: 在一家十二人中排行第六`). The *others* may be in-story (辞岁行/怀黍离) or LLM extrapolation — looks authoritative either way. → needs the LLM claim-grounding pass + chapter-level retrieval to confirm/refute each rank.
- **Event-level citations where chapter-level is needed**: every "（出自活动《怀黍离》）" should be "act31side : stage NN : line …". The page's own cites are too coarse to verify against. → needs per-stage summaries + a chapter-grounded auditor.
- **No fact card to anchor basics** — here 黍's basics happen to be right, but nothing *checked* them.
- **Meta-finding**: the v3 page *correctly avoided* the 黍/弟弟 error the assistant made — so the generation prompt is decent, but **nothing currently verifies it**; the uncited birth-order is where that luck runs out.

## 3. Root causes

1. **Retrieval is coarse + noisy.** `kb_query grep` returns raw stage *lines* across the whole corpus (noise); the alternative is the *event-level* `kb_summaries` (lossy). No good middle: per-`<章节>` summaries + chapter-scoped search. → agents skim and free-associate.
2. **No structured fact layer.** No per-char card (so `性别: 女` isn't where a check can see it); no relation graph with evidence (so an asserted relation can't be checked against anything; an *unnamed* relative can't be represented as a visible gap).
3. **No claim-tracing discipline** — in tooling (Phase-6 `kb_audit_wiki` not built) or in behaviour (nothing forces "every assertion ↔ a quotable raw line").
4. **The summaries themselves are unaudited LLM output** — they can carry the same hallucination, so they're not yet trustworthy retrieval targets.

## 4. Workstreams

### WS-1 — Retrieval: chapter granularity, searchable distilled layers

Raw data is already per-stage (`data/kb/events/<id>/stage_<NN>_<slug>.txt`, `<章节>`-wrapped). Add:

1. **Per-stage summaries.** `kb_summarize --stages` → `kb_summaries/stages/<event_id>/<NN>.md` with `<一句话概要> <核心剧情> <关键人物> <场景标签>` for *that chapter*. ~3500 stages, mostly single-pass; per-stage source-hash gate like today. Token cost ≈ one full `kb_summarize` bake — accepted. This is the "guided into individual chapter" layer: event → its stage one-liners → pick the chapter → pull raw `正文`.
2. **Searchable distilled layers.** `kb_query grep "<text>" --in {events,chars,summaries,stage_summaries,cards,relations,all}`. "岁兽" / "弟弟" first hits *distilled* text → `(event_id, stage_idx, line, source)`; drop to raw `正文` only when needed.
3. **`kb_query event stages <event_id>`** — list one event's chapters with their one-line summaries + `avgTag` + raw length. Target one chapter, not 14.
4. **Stage-scoped grep** — `--event <id>` / `--stage <id>:<NN>`.

### WS-2 — Structured fact layer: cards + relation network

**Fact cards** — `data/kb/cards/<char_id>.json`, deterministic from `character_table` + `handbook_info_table`: 代号/性别/种族/出身地/生日/身高/势力 + `客观履历` verbatim + storyset list + skin/module names, **each field with a source pointer**. Gitignored (regenerable in seconds). `kb_query card <char_id>`. Cheapest correctness anchor (kills any basics contradiction).

**Relation network** — two layers:
- *Co-occurrence graph* — deterministic, from char↔event **and char↔stage** edges → `data/kb/cooccurrence.jsonl`, weighted by shared stages. Navigation only.
- *Labeled relation assertions* — `kb_relations/<char_id>.jsonl`, **in git**, baked by `scripts/kb_relations.py` (LLM-extract → code-verify quote∈source & object `char resolve`s → reject otherwise), hash-gated, raw output archived. Source = the char's handbook sections **+ the per-`<章节>` chunks of events the char appears in** (chapter-level, not event summaries). Edge: `{a,b,relation,direction,evidence:[{source,quote}],confidence:curated|llm|cooccurrence}`. Taxonomy small: `kin`(sibling/parent/child/spouse) / `mentor`+`student` / `faction`+`colleague`+`subordinate` / `partner` / `friend` / `rival`+`antagonist` / `other`(+keyword+quote).
  - **Anti-hallucination rules**: object must resolve, else **no edge** — instead an `{subject, relation, object:null, hint:"会做饭的弟弟", evidence:"<line>"}` placeholder (TC-1, TC-2). Every edge carries verbatim evidence, code-rechecked. No transitive inference. Curated overrides (`arknights_lore_wiki/data/char_relations_curated.jsonl`, same role as `char_alias.txt`) win.
- Queries: `kb_query relations char|between|path|component <…>`. `relations component <一岁兽>` returns the named Sui + the `object:null` placeholders → the honest "5 named, ≥1 hinted, rest absent" answer.

### WS-3 — Validation: build Phase-6 `kb_audit_wiki`, chapter-grounded

`scripts/kb_audit_wiki.py` — read-only, never auto-edits — runs, per char (or story) page:

| Pass | Engine | Catches | TC |
|---|---|---|---|
| `tags` | code | missing/empty/truncated `char_wiki_tags` sections | — |
| `xref` | code | `<相关角色>` not `char resolve`d; `<相关活动>` not real event IDs | shu: 绩/颉/神农/禾生 |
| `entity_diff` | code | page-mentioned entity absent from every source `<章节>` line; prominent source entity missing from page | TC-1 |
| `card_check` | code | basic-info fields contradicting the deterministic card | TC-1 |
| `relation_check` | code | `<相关角色>`/`<详细介绍>` relationship assertions with no backing edge | TC-1 |
| `claim_grounding` | LLM, offloaded | per-claim `有依据/无依据/不确定` + **mandatory verbatim cite from supplied raw `<章节>` chunks**, code-rechecked; retrieval per claim = entity index → the specific stage chunks, *never* event summaries | TC-3, shu: birth-order |

Output: ranked punch list (card contradiction > unresolved xref > page-only entity > unsupported claim > coverage gap > stale), each line with its cite or its conspicuous lack of one. Fixes = alias add / targeted `get_char_wiki_v3 --char <id> --force` / manual edit, all user-approved. Budget knobs on `claim_grounding`: `--char`, `--max-claims`, `--claims-only`, `--audit-all` (off-budget). Per `docs/REQUIREMENTS.md` the hard caps already specified (30 claims/event, 15 omission candidates, 3 stages × 8K chars/call, ~150K-token soft budget) apply — but the user has authorized spending past them when needed.

**Also `kb_audit_summaries`** — same engine, source = the `<章节>` chunks a summary covers — over the 461 event summaries (+ new stage summaries). An unaudited summary is not a trustworthy retrieval target.

### WS-4 — Behavioural guardrails (`docs/AGENTS_GUIDE.md`, enforced in answers)

1. Every non-trivial lore assertion in an answer carries a verbatim citation (`event_id : stage_idx : line`). Can't cite → label 推断 / 未知. No exceptions.
2. Never characterize a character from `招募文本` alone — read `客观履历` (and the card) first.
3. Story summaries (`data/stories/*.txt`, `kb_summaries/`) are leads, not sources. Grounding cites point to raw `<章节>` text or handbook text. A claim whose only support is a summary is unverified.
4. No transitive/associative inference. Two facts about adjacent entities don't compose into a third unless a line says so. (黍 cooks + 年 has a cook-brother ≠ 黍 is the brother.)
5. When the source hints at an entity it doesn't name, say so explicitly — return the `object:null` shape, never a guess. Enumerations report named / hinted / unknown separately.
6. Read the `<章节>`, not the event summary, when a claim's truth depends on detail.

### WS-5 — Glue & ergonomics

- `kb_build` keeps cards + co-occurrence current (deterministic). LLM bakes (`kb_relations`, stage summaries, audits) are hash-gated separate runs (like `kb_summarize` today).
- `refresh-kb` skill gains the new bakes (hash-gated → re-running is free for unchanged content).
- New skill `audit-wiki-page` wrapping `kb_audit_wiki` with the human gates.
- Tests throughout: taxonomy validation; quote-in-source; object-resolves; **the TC-1 regression** ("unresolved object → null edge, never a guessed edge"); graph queries; audit-pass outputs on fixtures; **the TC-2 enumeration shape** (named/hinted/unknown buckets).

## 5. Phasing (each phase independently shippable, own branch/PR)

| # | Phase | Cost | Validates |
|---|---|---|---|
| P-A | Fact cards + searchable distilled layers (`cards/`, `kb_query card`, `grep --in`, `event stages`) | ~free (deterministic) | TC-1 basics-contradiction |
| P-B | Per-stage summaries (`kb_summarize --stages`, `kb_summaries/stages/`) | ≈ one bake | chapter-level retrieval (the explicit ask) |
| P-C | Relation network (`cooccurrence.jsonl`, `kb_relations.py`, `kb_relations/`, curated override, `kb_query relations …`) | ≈ one handbook+appeared-chapters pass per char | TC-1 (null edge), TC-2 |
| P-D | `kb_audit_wiki` (Phase 6): all passes, chapter-grounded `claim_grounding` | code free; LLM per-page deliberate | TC-3 |
| P-E | Audit existing artifacts: `kb_audit_summaries` over 461 summaries; `kb_audit_wiki` over char_v3 pages → punch list | the big recurring spend; page/batch at a time, user-gated | — |
| P-F | Guardrails doc + skills (`AGENTS_GUIDE.md` rules, `refresh-kb` update, `audit-wiki-page` skill) | ~free | — |

## 6. Token posture

- Deterministic layers (cards, co-occurrence, `tags`/`xref`/`entity_diff`/`card_check`/`relation_check`): ~free, run always.
- LLM bakes (stage summaries, relation extraction): each ≈ one `kb_summarize`-scale spend; one-time + hash-gated thereafter. Do them.
- `claim_grounding`: the recurring cost. Scoped per page/summary, `--max-claims`, retrieval kept tight (the *specific* stage chunks per claim). Run it on anything before trusting it — that's the point; spend is authorized.

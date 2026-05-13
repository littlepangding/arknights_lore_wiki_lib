# Plan: improving lore search & validation accuracy

> Status: proposal, rev. 3 (2026-05-12). P-A shipped (`kb-fact-cards`); P-B / WS-0 shipped (`kb-participants`) — char↔stage edges are now three layers (deterministic / participant-with-`tier` / event-scoped summary) with `--min-tier`. P-C in flight (`kb-stage-summaries`) — `kb_summarize --stages` bakes one summary per `<章节>` to `kb_summaries/stages/<event_id>/<NN>.md` (code + tests shipped, sharing a hash-gated/resume-safe `_run_batch` with the event bake; the ~1937-stage LLM run is the user's to do, and the `<关键人物>`→stage-granular `summary`-edge upgrade is a tracked follow-up — see the P-C row). Rev. 2 added **WS-0 — tiered participant extraction** after a review pass found the char↔stage mapping too coarse to trust (substring-grep `count`, no speaker awareness, flat output). Rev. 3 closes contract gaps a second review found: (a) **named non-operator entities** are first-class — a relation object / citation target is `{name, entity_type, char_id|null}`; `object:null` is reserved for the genuinely *unnamed*; (b) a fixed **citation-source taxonomy** spanning event chapters *and* a char's own handbook/voice/card; (c) `source` and `tier` are separate fields on participant edges (`source ∈ {storyset, participant, summary}` always passes any `--min-tier`); (d) `<关键人物>` summary edges are event-scoped until per-stage summaries land; (e) corrected stage count (1937, not ~3500) and a swapped phase reference; (f) real per-script word-boundary logic for short names (Python `\b` is wrong here). Driven originally by a concrete failure (assistant stitched "黍 is the cook 弟弟 年 mentioned" from two unrelated facts, contradicting 黍's own 性别/排行) plus a manual audit of `arknights_lore_wiki/data/char_v3/char_2025_shu.txt`. Token cost is acknowledged but secondary to correctness: retrieval should reach raw chapter text, guided per-`<章节>`, not per-event-summary.

## 1. Test cases (these define "correct")

**TC-1 — Hallucinated relation (黍 / 弟弟).** "黍 是年口中那个会做饭的弟弟" is false: 年's voice line names an *unnamed* 弟弟 (male); 黍 is 女 and is a *姐姐* to 年. Acceptance:
- Q&A: "年口中会做饭的弟弟是谁?" → "未具名 — 年语音提到一个会做饭的弟弟（cite），无任何文本指认其身份；黍为女性、年的姐姐（cite），排除。"
- Validation: a page asserting this claim is flagged with the missing-link evidence.

**TC-2 — Gap-aware enumeration (十二岁兽).** Acceptance: "列出年家十二姐妹" → *named* set with char_id + cite each, *hinted-but-unnamed* set with cite each, and a *not-yet-revealed* count. Never padded to 12 with guesses. (From the wiki today the named-ish set is already ~重岳/望/绩/黍/年/夕/令/颉 — and several of their *排行* numbers are uncited; the answer must say so.)

**TC-3 — Char-page claim tracing.** Acceptance: given `char_v3/<file>.txt`, emit (i) structural/xref/coverage/card/relation flags from code, (ii) a per-claim `有依据/无依据/不确定` table where every "有依据" carries a *verbatim* quote from a *raw `<章节>` chunk* (never from a story summary), code-rechecked.

**TC-4 — Trustworthy "who's in this chapter" (the rev-2 addition).** Acceptance: `kb_query event chars <id>` (and `stage_chars`) returns, per char, a *tier* — `speaker` (had ≥1 line of dialogue) / `named` (named in narration) / `mentioned` (lone passing reference) — and the downstream consumers (relation co-occurrence, audit `entity_diff`, Q&A "who appears in X") all threshold on it instead of treating a single noisy substring hit as presence. Concretely: 年 in an event whose prose says "今年" but where 年 never speaks must *not* surface as a participant of that stage.

## 2. What the manual audit of `char_2025_shu.txt` already shows (gaps in current tooling)

Running passes a/b/c by hand against the raw KB:

- **Unresolved `<相关角色>`**: `绩`, `颉`, `神农` don't `char resolve` — but they're **named entities**, not unknowns; the resolver just only knows playable operators. `禾生` resolves to `char_4119_wanqin` — almost certainly a mis-resolution (a curated alias pointing the wrong way, or a name collision), needs a human check. → an `xref` pass catches all of these *today* with `kb_query char resolve`; we just haven't built it. **But the deeper fix (rev. 3): `char resolve` returning "missing" must not be read as "unnamed"** — 绩/颉/神农 are exactly the named-but-not-an-operator entities TC-2 needs represented (see WS-2's `{name, entity_type, char_id|null}` shape and the entity-id layer below).
- **Uncited structured claims**: the page asserts a full Sui birth-order — 重岳=长子, 望=第二, 绩=第三, 黍=第六, 年=第九, 夕=第十一. 黍=第六 is grounded (`档案资料一: 在一家十二人中排行第六`). The *others* may be in-story (辞岁行/怀黍离) or LLM extrapolation — looks authoritative either way. → needs the LLM claim-grounding pass + chapter-level retrieval to confirm/refute each rank.
- **Event-level citations where chapter-level is needed**: every "（出自活动《怀黍离》）" should be "act31side : stage NN : line …". The page's own cites are too coarse to verify against. → needs per-stage summaries + a chapter-grounded auditor.
- **No fact card to anchor basics** — here 黍's basics happen to be right, but nothing *checked* them. → P-A (shipped) now provides `kb_query char card`.
- **The char↔stage edges feeding any "appears in" check are noisy.** `禾生`'s edges are whatever `name == "禾生"` substring-matched, plus 黍's inferred edges are `body.count("黍")` — which also counts 黍 inside compound words and quoted prose. An `entity_diff` pass built on top of *that* would inherit the noise. → WS-0.
- **Meta-finding**: the v3 page *correctly avoided* the 黍/弟弟 error the assistant made — so the generation prompt is decent, but **nothing currently verifies it**; the uncited birth-order is where that luck runs out.

## 3. Root causes

1. **Retrieval is coarse + noisy.** `kb_query grep` returns raw stage *lines* across the whole corpus (noise); the alternative is the *event-level* `kb_summaries` (lossy). No good middle: per-`<章节>` summaries + chapter-scoped search. → agents skim and free-associate.
2. **The char↔stage mapping is a flat substring count.** `indexer.build_char_to_events_inferred` does `body.count(alias)` per stage with a 13-word blocklist + a per-class length floor, then subtracts deterministic pairs. It ignores the *speaker* signal that's already in the chunk text (`clean_script` rewrites `[name="年"]台词` → `年：台词`), it's substring not token (`年`⊂`年龄`, `W`⊂`World`, `Pith/Touch/Sharp` are English words), and it emits only `count`+`match_class` — no confidence tier a consumer can threshold on. So "who's in this chapter" — the input to relation co-occurrence, audit `entity_diff`, and "who appears in X" Q&A — is unreliable, and nothing built on it can be more reliable than it is.
3. **No structured fact layer, and no entity notion beyond "playable operator".** No relation graph with evidence (so an asserted relation can't be checked against anything; an *unnamed* relative can't be represented as a visible gap). And `kb_query char resolve` only resolves operator names → a named-but-non-operator entity (绩, 颉, 神农, most NPCs) currently has no id and no honest representation — it's neither "an operator" nor "unnamed". (Fact cards: shipped in P-A.)
4. **No claim-tracing discipline** — in tooling (Phase-6 `kb_audit_wiki` not built) or in behaviour (nothing forces "every assertion ↔ a quotable raw line").
5. **The summaries themselves are unaudited LLM output** — they can carry the same hallucination, so they're not yet trustworthy retrieval targets.

## 4. Workstreams

### WS-0 — Participant extraction: tiered char↔stage edges (the foundation)

Replace the substring-count inferred pass with a **per-stage participant extractor**. Each edge row carries two orthogonal fields — **`source`** (where the edge came from) and **`tier`** (how strongly the stage uses the char):

- `source ∈ {storyset, participant, summary}`:
  - **`storyset`** — deterministic, from `char.storysets` (the operator's own dedicated story arcs). Ground truth; **always passes any `--min-tier` filter** regardless of its `tier`.
  - **`participant`** — derived by the extractor below from the cleaned chunk text.
  - **`summary`** — from the `<关键人物>` tag of `kb_summaries`, each name resolved through the alias index. "The LLM read the whole chapter and said these people matter" — catches chars referred to only by title/nickname a canonical grep misses; hash-gated free (no new LLM calls, just reads the baked `.md`). **Event-scoped in P-B** (event-level `<关键人物>` has no honest `stage_idx`) → surfaced in `event_chars` only, *not* joined into `stage_chars`/`char_appearances`; P-C's per-stage summaries upgrade it to stage granularity.
- `tier ∈ {speaker, named, mentioned}` (for `participant` source; `storyset`/`summary` rows record their best-available tier but bypass the filter / are event-scoped respectively):
  - **`speaker`** — the chunk text has ≥1 line-leading `名字[:：]…` dialogue line for one of the char's aliases (the speaker list is already materialized by `clean_script`'s `[name="X"]Y → X:Y` rewrite, incl. `multiline(name=…)` — we're sitting on it). Highest precision; this is what "appears in" should mean by default.
  - **`named`** — an alias appears in narration: a multi-char canonical name, or `count ≥ N` (N≈2). **Short-name handling needs real boundary logic, not Python `\b`** — in `re`, CJK chars are `\w`, so `\b年\b` matches `年` only when *both* neighbours are non-word: it correctly rejects `今年` but *also* rejects `年走进门` (`走` is `\w`), which is the common case in spaceless Chinese prose; `\bW\b` likewise rejects `W走出帐篷`. So: ASCII aliases — neighbours must not be `[A-Za-z0-9]` (excludes `World`, keeps `W走出帐篷`); single-CJK aliases — don't trust narration grep on its own at all: require `speaker` tier, or `count ≥ 2`, or a `summary`-source hit to promote a single-CJK name above `mentioned`.
  - **`mentioned`** — a lone short/fuzzy/single-count hit and nothing stronger. Kept (recall floor) but tier-marked so consumers can drop it.

Output shape (replaces today's inferred rows): `{char_id, event_id, stage_idx|null, source, tier, spoke_lines, mention_count, matched_aliases}` (`stage_idx` is `null` for event-scoped `summary` rows).

`event_chars` / `stage_chars` / `char_appearances` gain `source` + `tier`; `kb_query` exposes `--min-tier {speaker,named,mentioned}` (default `named`) — `source=storyset` rows always pass. **This is foundation work, not a detour**: the same extractor is exactly what WS-2's co-occurrence matrix consumes (two `speaker`s in one stage = a real char↔char edge), and what WS-3's `entity_diff` thresholds on. Tests: speaker-line parsing (incl. `multiline(name=…)`); boundary matching **both directions** — `今年`/`World` excluded, `年走进门`/`W走出帐篷` *included*; tier precedence; `source=storyset` bypasses `--min-tier`; the TC-4 regression (mention-only char with the noisy compound-word case must not surface as a stage participant); `summary`-source resolution + the unresolved-name drop + its event-scoping.

### WS-1 — Retrieval: chapter granularity, searchable distilled layers

Raw data is already per-stage (`data/kb/events/<id>/stage_<NN>_<slug>.txt`, `<章节>`-wrapped). Add:

1. **Per-stage summaries.** `kb_summarize --stages` → `kb_summaries/stages/<event_id>/<NN>.md` with `<一句话概要> <核心剧情> <关键人物> <场景标签>` for *that chapter*. **1937 stages** across 461 events (`find data/kb/events -name 'stage_*.txt' | wc -l`), mostly single-pass; per-stage source-hash gate like today. Token cost ≈ one full `kb_summarize` bake (a bit less — it's per-chapter not per-event-with-multipass) — accepted. This is the "guided into individual chapter" layer: event → its stage one-liners → pick the chapter → pull raw `正文`. (Also: its `<关键人物>` tags upgrade WS-0's `summary`-source edges from event-scoped to *stage* granularity.)
2. **Searchable distilled layers.** `kb_query grep "<text>" --in {events,chars,summaries,stage_summaries,cards,relations,all}`. "岁兽" / "弟弟" first hits *distilled* text → `(event_id, stage_idx, line, source)`; drop to raw `正文` only when needed. (P-A shipped `--in summaries`; the rest land with their layers.)
3. **`kb_query event stages <event_id>`** — shipped (P-A): list one event's chapters with `avgTag` + raw length; gains the one-line summaries once P-C lands.
4. **Stage-scoped grep** — `--event <id>` / `--stage <id>:<NN>`.

### WS-2 — Structured fact layer: cards + relation network

**Fact cards** — `data/kb/chars/<char_id>/card.json`, deterministic from `character_table` + `handbook_info_table`: 名称/appellation/nationId + parsed `基础档案` (代号/性别/种族/出身地/生日/身高/…) + parsed `综合体检测试` + `客观履历` verbatim + archive-section list + skin/module names + storyset list, **each field with a source pointer**. Gitignored (regenerable in seconds by `kb_build`). `kb_query char card <char_id>`. **Shipped in P-A.** Cheapest correctness anchor (kills any basics contradiction).

**Entities** (prerequisite — settle this before P-D). The relation graph's nodes are *entities*, not just operators. An entity reference is `{name, entity_type, char_id|null}`:
- `char_id` set + `entity_type="operator"` — resolved via `kb_query char resolve` as today.
- `char_id=null` + `entity_type ∈ {npc, organization, location, group, other}` + `name="绩"` — a **named** entity that isn't a playable operator (绩, 颉, 神农, 罗德岛, …). These get a stable synthetic id (`ent_<sha>` of the normalized name, mirroring `get_char_file_name`'s sha fallback) so the graph can have a node for them; they're listed in `data/kb/entities.jsonl` (deterministic — seeded from `character_table` non-playable rows + `<关键人物>` tags + curated `char_alias.txt` lines, hash-gated free). Curated additions go in `arknights_lore_wiki/data/entities_curated.jsonl`.
- `name=null` — only for the genuinely *unnamed*: "年口中会做饭的弟弟" carries `hint:"会做饭的弟弟"` and no name/id (TC-1).

So "did `char resolve` return missing?" is **not** the gate — "is there a name?" is. A named NPC becomes a real (`char_id:null`) node; only an unnamed referent becomes a `null` placeholder.

**Relation network** — two layers:
- *Co-occurrence graph* — deterministic, **built from WS-0's tiered char↔stage edges** (weight by shared stages where both are `speaker` ≫ shared stages where one is only `mentioned`) → `data/kb/cooccurrence.jsonl`. Nodes are entities (operators + named NPCs). Navigation only — co-occurrence is not a relation.
- *Labeled relation assertions* — `kb_relations/<char_id>.jsonl`, **in git**, baked by `scripts/kb_relations.py` (LLM-extract → code-verify quote∈source & each end resolves to an entity *or* is a named `char_id:null` entity *or* is a `name:null` hint → reject anything else), hash-gated, raw output archived. Source = the char's handbook sections **+ the per-`<章节>` chunks of events where WS-0 puts the char at `speaker`/`named` tier** (chapter-level, not event summaries). Edge: `{a, b, relation, direction, evidence:[{source, quote}], confidence:curated|llm|cooccurrence}` where `a`/`b` are entity refs as above. Taxonomy small: `kin`(sibling/parent/child/spouse) / `mentor`+`student` / `faction`+`colleague`+`subordinate` / `partner` / `friend` / `rival`+`antagonist` / `other`(+keyword+quote).
  - **Anti-hallucination rules**: a relation end is either (i) a resolved operator, (ii) a named non-operator entity (`char_id:null`, `name` set — a real node), or (iii) a `name:null` hint placeholder `{subject, relation, object:{name:null, hint:"会做饭的弟弟"}, evidence:"<line>"}`. Never invent a `char_id`; never drop a *named* end to `null`. Every edge carries verbatim evidence, code-rechecked. No transitive inference. Curated overrides (`arknights_lore_wiki/data/char_relations_curated.jsonl`, same role as `char_alias.txt`) win.
- Queries: `kb_query relations char|between|path|component <…>`. `relations component <一岁兽>` returns the named Sui (operators + `char_id:null` named NPCs like 绩/颉) + the `name:null` hint placeholders → the honest "5 named, ≥1 hinted, rest absent" answer (TC-2).

### WS-3 — Validation: build Phase-6 `kb_audit_wiki`, chapter-grounded

`scripts/kb_audit_wiki.py` — read-only, never auto-edits — runs, per char (or story) page:

| Pass | Engine | Catches | TC |
|---|---|---|---|
| `tags` | code | missing/empty/truncated `char_wiki_tags` sections | — |
| `xref` | code | `<相关角色>` not `char resolve`d; `<相关活动>` not real event IDs | shu: 绩/颉/神农/禾生 |
| `entity_diff` | code | page-mentioned entity absent from this page's source `<章节>` chunks; **prominent source entity (`speaker`/`named` tier per WS-0) missing from the page** | TC-1, TC-4 |
| `card_check` | code | basic-info fields contradicting the deterministic card | TC-1 |
| `relation_check` | code | `<相关角色>`/`<详细介绍>` relationship assertions with no backing edge | TC-1 |
| `claim_grounding` | LLM, offloaded | per-claim `有依据/无依据/不确定` + **mandatory verbatim cite from a supplied source chunk**, code-rechecked; retrieval per claim = entity index → the relevant chunks (WS-0 tiers pick which event chapters), *plus the char's own handbook/voice sections and fact card*, *never* event/stage summaries | TC-3, shu: birth-order |

**Citation-source taxonomy** (rev. 3 — `claim_grounding` and WS-4 rule 1 both use this; a cite is one of these forms, never a story/stage *summary*):
- `event:<event_id>:stage:<NN>:line <n>` — a raw `<章节>` line.
- `char:<char_id>:archive:<section_name>` — a handbook archive section (`基础档案` / `客观履历` / `档案资料一` / …). 黍's 排行第六 lives here, not in any event chapter.
- `char:<char_id>:voice:<charword_key>` — a voice line.
- `card:<char_id>:<field>` — a deterministic fact-card field (`性别`, `nationId`, …) — the cheapest, already code-checked by `card_check`.

`claim_grounding`'s context bundle for a char page = the char's card + handbook sections + the event-chapter chunks where WS-0 puts the char at `speaker`/`named` tier; for a story page = that story's `<章节>` chunks + the cards of its named participants. A claim grounded only in handbook/card text is *supported* — don't mark dossier-derived claims unsupported just because they aren't in an event chapter.

Output: ranked punch list (card contradiction > unresolved-or-misresolved xref > page-only entity > unsupported claim > coverage gap > stale), each line with its cite (in the taxonomy above) or its conspicuous lack of one. Fixes = alias add / entity-curated add / targeted `get_char_wiki_v3 --char <id> --force` / manual edit, all user-approved. Budget knobs on `claim_grounding`: `--char`, `--max-claims`, `--claims-only`, `--audit-all` (off-budget). Per `docs/REQUIREMENTS.md` the hard caps already specified (30 claims/event, 15 omission candidates, 3 stages × 8K chars/call, ~150K-token soft budget) apply — but the user has authorized spending past them when needed.

**Also `kb_audit_summaries`** — same engine, source = the `<章节>` chunks a summary covers — over the 461 event summaries (+ new stage summaries). An unaudited summary is not a trustworthy retrieval target.

### WS-4 — Behavioural guardrails (`docs/AGENTS_GUIDE.md`, enforced in answers)

1. Every non-trivial lore assertion in an answer carries a verbatim citation in the §WS-3 taxonomy (`event:…:stage:…:line`, `char:…:archive:…`, `char:…:voice:…`, or `card:…`). Can't cite → label 推断 / 未知. No exceptions.
2. Never characterize a character from `招募文本` alone — read `客观履历` (and the card) first.
3. Story / stage summaries (`data/stories/*.txt`, `kb_summaries/`) are leads, not sources — never a citation target. Grounding cites point to raw `<章节>` text or to handbook/voice/card text. A claim whose only support is a summary is unverified.
4. No transitive/associative inference. Two facts about adjacent entities don't compose into a third unless a line says so. (黍 cooks + 年 has a cook-brother ≠ 黍 is the brother.)
5. Distinguish three cases for a referenced entity: (i) a known operator → cite its `char_id`; (ii) a *named* non-operator → name it, `char_id:null`, it's still a real entity; (iii) only *hinted*, no name → return the `name:null`/`hint` shape, never a guess. Enumerations report named (i+ii) / hinted (iii) / not-yet-revealed separately.
6. Read the `<章节>` (or the handbook section), not the event summary, when a claim's truth depends on detail.
7. "Appears in event X" means `speaker`/`named` tier (WS-0) by default — a lone `mentioned` hit is "name-dropped", say so.

### WS-5 — Glue & ergonomics

- `kb_build` keeps cards + `entities.jsonl` + WS-0 participant edges + co-occurrence current (all deterministic). LLM bakes (`kb_relations`, stage summaries, audits) are hash-gated separate runs (like `kb_summarize` today).
- `refresh-kb` skill gains the new bakes (hash-gated → re-running is free for unchanged content).
- New skill `audit-wiki-page` wrapping `kb_audit_wiki` with the human gates.
- Tests throughout: WS-0's parsing/tiers/TC-4 regression + boundary matching **both directions**; entity-ref shape (operator / named-`char_id:null` / `name:null`-hint) + `entities.jsonl` synthetic-id stability; relation-taxonomy validation; quote-in-source; **the TC-1 regression** ("unnamed object → `name:null` hint, never a guessed `char_id` and never dropping a *named* end"); graph queries; audit-pass outputs on fixtures incl. a handbook-grounded claim being marked *supported*; **the TC-2 enumeration shape** (named / hinted / not-yet-revealed buckets).

## 5. Phasing (each phase independently shippable, own branch/PR)

| # | Phase | Cost | Validates | Status |
|---|---|---|---|---|
| P-A | Fact cards + searchable distilled layers (`chars/*/card.json`, `kb_query char card`, `grep --in summaries`, `event stages`) | ~free (deterministic) | TC-1 basics-contradiction | **shipped** (`kb-fact-cards`) |
| P-B | **WS-0** — tiered participant extractor (`build_stage_participants`, `source`+`tier` on `event_chars`/`stage_chars`/`char_appearances`, `--min-tier`, event-scoped `summary`-source edges from `<关键人物>`) | ~free (deterministic) | TC-4; unblocks P-E `entity_diff` + P-D co-occurrence | **shipped** (`kb-participants`) |
| P-C | Per-stage summaries (`kb_summarize --stages`, `kb_summaries/stages/<event_id>/<NN>.md`) | ≈ one bake (a touch less — per-chapter, no multipass; ~1937 calls, ~13M tok by `--stages --estimate`) | chapter-level retrieval (the explicit ask); the `<关键人物>`→stage-granular `summary`-edge upgrade is a follow-up (indexer reads `kb_summaries/stages/` — no behaviour change until the bake exists) | **code shipped** (`kb-stage-summaries`); LLM bake + edge upgrade pending |
| P-D | Entity layer (`entities.jsonl`, `{name, entity_type, char_id\|null}` refs, curated override) **then** relation network (`cooccurrence.jsonl` from WS-0 edges, `kb_relations.py`, `kb_relations/`, curated override, `kb_query relations …`) — settle the entity contract first | entity layer ~free (deterministic); relations ≈ one handbook+appeared-chapters pass per char | TC-1 (`name:null` hint, never a guessed id), TC-2 (named-non-operator nodes) | |
| P-E | `kb_audit_wiki` (Phase 6): all passes, chapter-grounded `claim_grounding` | code free; LLM per-page deliberate | TC-3 | |
| P-F | Audit existing artifacts: `kb_audit_summaries` over 461 summaries; `kb_audit_wiki` over char_v3 pages → punch list | the big recurring spend; page/batch at a time, user-gated | — | |
| P-G | Guardrails doc + skills (`AGENTS_GUIDE.md` rules incl. tier semantics, `refresh-kb` update, `audit-wiki-page` skill) | ~free | — | |

(Ordering notes: P-B before P-C is the recommendation — WS-0 is deterministic/free, and both the relation graph and the audit `entity_diff` are only as good as the edges underneath them. P-C is the "explicit ask" for chapter retrieval; doing it second means its `<关键人物>` tags can immediately upgrade WS-0's `summary`-source edges to stage granularity. If you'd rather have the chapter-summary layer in hand first, P-B and P-C swap cleanly. Within P-D the **entity layer ships before the relation bake** — the relation graph's node/edge contract depends on it, and getting it wrong is the gap rev. 3 exists to close. The entity layer itself is deterministic and could even be pulled forward next to P-B if convenient.)

## 6. Token posture

- Deterministic layers (cards, `entities.jsonl`, WS-0 participant edges, co-occurrence, `tags`/`xref`/`entity_diff`/`card_check`/`relation_check`): ~free, run always.
- LLM bakes (stage summaries, relation extraction): each ≈ one `kb_summarize`-scale spend; one-time + hash-gated thereafter. Do them.
- `claim_grounding`: the recurring cost. Scoped per page/summary, `--max-claims`, retrieval kept tight (the *specific* stage chunks per claim — WS-0 tiers pick which). Run it on anything before trusting it — that's the point; spend is authorized.

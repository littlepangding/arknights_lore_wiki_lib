# Codex Review 07 (Independent Follow-up)

Scope: reviewed the current `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/DESIGN.md` and `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/AGENTS_GUIDE.md` against `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/REQUIREMENTS.md`.

## Findings

### [P1] Omission audit is still operator-centric, so NPC-only scenes can slip through

The new two-signal audit fixes the earlier "entity diff only" problem, but Signal 1 still extracts entities by grepping **canonical operator names + curated operator aliases** from the raw text (`DESIGN.md:548-553`). That is much narrower than the requirements-level promise to flag missing scenes in general, because the design's own v1 scope says NPCs, titles, groups, and other non-operator entities are not first-class (`DESIGN.md:11-18`).

This matters because Signal 2 only checks **claims already present in the summary** (`DESIGN.md:555-562`). If a summary entirely omits an NPC-led scene, Signal 2 never sees a claim for it, and Signal 1 will also miss it if the omitted scene does not mention an operator that the index recognizes. In other words: a whole scene can still disappear without being flagged, which falls short of the audit target in `REQUIREMENTS.md:18-20` and `REQUIREMENTS.md:55`.

Recommendation: either broaden Signal 1 beyond operator-centric entities, or narrow the stated audit contract so it does not promise general missing-scene detection.

### [P2] `match_class` is recorded internally but not exposed through the public query contract

The revised inferred-edge design now records a per-edge `match_class` and explicitly relies on consumers using it to downweight noisy single-character canonical hits (`DESIGN.md:268-275`, `DESIGN.md:672`). But the public retrieval API does not carry that field through: `Appearance` is still documented as only `(event_id, stage_idx_or_none, source, count_or_none)` (`DESIGN.md:315-340`), and the advertised CLI commands for `char appearances` / `event chars` / `stage_chars` have no way to surface `match_class` either (`DESIGN.md:513-523`).

That leaves the one-character-name fix incomplete. The design now accepts recall-oriented noisy hits for names like `陈` and `年`, but the agent-facing interface discards the very signal that is supposed to let callers judge those hits safely.

Recommendation: make `match_class` part of `Appearance` and expose it in `kb_query` output, or stop describing it as a consumer-facing mitigation.

### [P2] The onboarding example still teaches an impossible resolver flow

`AGENTS_GUIDE.md` correctly says `kb_query char resolve <name>` is operator-only and that `Missing` is common for NPCs and other non-operator entities (`AGENTS_GUIDE.md:44-54`). But the very next Q/A example tells the agent to resolve `司辰` into a `char_id` and continue through `char storysets` / `char appearances` (`AGENTS_GUIDE.md:89-95`).

In the current corpus, `司辰` does not appear in `character_table.name`, `character_table.appellation`, `char_alias.txt`, or raw game data, so that flow will not work. This makes the progressive-disclosure path misleading for future agents and clashes with the "agent dropped into the repo can find its way" requirement in `REQUIREMENTS.md:13-14`.

Recommendation: replace the example with a resolvable operator name, or rewrite it to demonstrate the documented `Missing -> grep` fallback path.

# Codex Review 08 (Independent Follow-up)

Scope: reviewed the current `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/DESIGN.md`, `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/AGENTS_GUIDE.md`, and `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/README.md` against `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/REQUIREMENTS.md`.

## Findings

### [P2] NPC-omission signal still excludes non-Han / punctuated entity names the guide claims are retrievable

Signal 1 now broadens omission detection with an NPC-shaped heuristic, but the rule is still restricted to Han-only 2-6 character tokens (`DESIGN.md:559-566`). That leaves out entity names with punctuation or Latin script, including examples the guide explicitly presents as supported literal-grep cases: `AUS (群体)`, `Ishar-mla`, and `"桥夹"克里夫` (`AGENTS_GUIDE.md:54`, `:108`).

So the docs currently promise more retrieval coverage than the omission-audit heuristic can actually use. If a summary drops a scene centered on one of those entities, Signal 1 still has a blind spot unless some other candidate in the same scene survives the operator/NPC filters.

Recommendation: either broaden the NPC-shaped candidate extractor beyond `[一-鿿]{2,6}`, or document that Signal 1 only covers a narrower entity class than general `grep` retrieval does.

### [P2] `char appearances` still has two different CLI contracts in the docs

`DESIGN.md` documents the public query API as `char_appearances(kb, char_id, ...)` and the CLI as `kb_query.py char appearances <char_id>` (`DESIGN.md:321-323`, `:528-530`). But `AGENTS_GUIDE.md` advertises `.venv/bin/python -m scripts.kb_query char appearances <char_id_or_name>` (`AGENTS_GUIDE.md:78`).

That is a real contract mismatch for the agent-facing entry point. If name support is intended at the CLI wrapper level, the design doc should say so explicitly. If not, the guide should stop implying the command accepts unresolved names directly.

Recommendation: pick one contract and state it consistently in both docs.

### [P3] The measured single-character-operator count has drifted from the current corpus

The docs still say the one-character-name fix restores **23** operators (`DESIGN.md:268`, `AGENTS_GUIDE.md:52`, `README.md:41`). In the current corpus there are **24** `char_*` operators with one-character `name` fields.

This is not a structural design bug, but the KB docs lean heavily on measured corpus facts elsewhere, so keeping the count accurate matters for trust in the surrounding sizing / coverage claims.

Recommendation: update the count from 23 to 24 anywhere it is presented as a measured fact.

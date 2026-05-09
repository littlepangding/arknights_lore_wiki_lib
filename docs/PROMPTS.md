# LLM Prompts (Chinese)

> Drafts of every Chinese prompt used by `libs/kb/summarize.py` and `scripts/kb_audit_wiki.py`. Reviewable in isolation. None of these have been run yet — they're proposals.

## Conventions

- All prompts are written in 简体中文 to match the source material.
- Output uses `<标签>...</标签>` blocks — same convention as the existing `story_wiki_tags` / `char_wiki_tags`, validated by `extract_tagged_contents`.
- System prompt establishes role + constraints. User prompt feeds context.
- Hard rules ("不要编造", "严格使用简体中文") are repeated at the bottom because models routinely drift in long contexts.
- Output is small by design: short summaries, not full retellings. This keeps `kb_summaries/` tiny and limits how much raw content is reproduced.

---

## P1 — Per-event summary (committed to `kb_summaries/events/<id>.md`)

**System:**
```
你是一个明日方舟剧情资料编写助手。你的任务是阅读活动剧情原文，输出结构化的导航摘要，仅供索引和检索，不替代原文。你严格遵守输出格式，使用简体中文，不引申、不评价、不揣测原作未交代的内容。
```

**User (single-pass, total event text under threshold):**
```
以下是明日方舟某次活动的全部剧情原文（按章节组织）。请基于原文输出以下内容：

<一句话概要>
不超过40字，概括活动主题。
</一句话概要>

<核心剧情>
约300字的剧情梗概，按时间顺序，不引申、不评价、不揣测原作未交代的内容。
</核心剧情>

<关键人物>
用分号分隔的人物名单。仅限在剧情中实质出场或被关键提及的角色，不收录"博士"、"罗德岛"等非角色实体。
</关键人物>

<场景标签>
3-6个简短词组（用分号分隔），覆盖主要场景、地点或事件类型。
</场景标签>

【硬性要求】
- 严格使用简体中文，不要使用繁体或日文汉字。
- 不要在输出标签之外添加解释或对白。
- 如果某一项无法从原文中得出，写"无"，不要编造。
- 摘要的总长度控制在 600 字以内（不含标签）。

剧情原文：
<<<EVENT_TEXT>>>
```

**User (multi-pass, used when `total_length > 80,000` chars OR `stage_count > 10` — first reduce per-stage, then merge):**

> Threshold lowered from `> 200 KB` per Codex review 04 finding 2 / review 05 finding 2 — the old cutoff hit only 2 events and defeated the purpose of multi-pass. The new union catches ~70-90 events. M5 in `DESIGN.md` shows the distribution.


Per-stage reduction prompt is the same shape but emits only `<章节概要>` (200 字以内) and `<本章人物>`. The merge prompt feeds the concatenation of stage outputs and asks for the same tags as the single-pass prompt, with an extra hint: "以下输入已是分章摘要，请基于它们重写整体摘要，不要逐章罗列。"

Required output tags (validated): `一句话概要`, `核心剧情`, `关键人物`, `场景标签`.

---

## P2 — Per-character one-liner (DEFERRED, not used in v1)

> Per Codex review 04 finding 3: dropped from v1 because char data is already sectional and small (median ~5 KB, max ~11 KB) and `manifest.json` carries the structured navigation aids. Keeping the prompt here for possible v2 reuse.


**System:** identical to P1.

**User:**
```
以下是明日方舟某位角色的全部公开资料（招募文本、语音、档案、皮肤旁白、模组描述）。请基于资料输出：

<一句话简介>
不超过30字，概括角色身份与定位。
</一句话简介>

<所属势力>
若资料中提及，写出势力名称（如：罗德岛、维多利亚、卡西米尔）。否则写"无"。
</所属势力>

<关键词>
3-6个简短词组（分号分隔），覆盖角色关键属性、剧情角色或代表性事件。
</关键词>

【硬性要求】
- 严格使用简体中文。
- 不要在输出标签之外添加解释。
- 不要编造资料中未出现的内容。

角色资料：
<<<CHAR_TEXT>>>
```

Required tags: `一句话简介`, `所属势力`, `关键词`.

---

## P3 — Audit a story summary (two-signal flow, `scripts/kb_audit_wiki.py --target story`)

The audit runs two complementary prompts (per `DESIGN.md` audit section). P3a verifies omission candidates surfaced by Signal 1 (entity-coverage diff). P3b verifies individual claims surfaced by Signal 2 (claim-level coverage). Same-stage candidates / claims may be batched into a single call but the schema is per-item.

### P3a — Omission verification (Signal 1)

Input: a candidate entity (operator or NPC-shaped name) flagged as present in raw but absent from the summary, plus the raw stages where it appears, plus the summary.

**System:**
```
你是一个明日方舟剧情资料审核员。你的任务是判断一份已生成的剧情摘要是否遗漏了原文中的某个角色或场景。你严格根据原文判断，不引申、不补全。
```

**User:**
```
以下是某次活动的部分原文片段（候选实体出现的章节），以及已生成的剧情摘要。请判断：候选实体在原文中的出场是否构成对剧情理解有实质影响的内容？如果是，摘要里是否覆盖到了？

候选实体：<<<CANDIDATE_ENTITY>>>

请使用以下格式输出：

<判断>
属于遗漏 / 不属于遗漏 / 不确定
</判断>

<理由>
不超过60字。如属于遗漏，说明原文中该实体的关键行动；如不属于，说明原文中该实体仅是枝节出现或摘要已有等价描述。
</理由>

<原文证据>
原文中的简短直接引用（如有）。无则写"无"。
</原文证据>

【硬性要求】
- 严格使用简体中文。
- 不要在输出标签外添加内容。
- 不要将"博士"、"罗德岛"等非角色实体判定为遗漏。

原文片段：
<<<RAW_CHUNKS>>>

已生成摘要：
<<<EXISTING_SUMMARY>>>
```

Required tags: `判断`, `理由`, `原文证据`.

### P3b — Per-claim verdict (Signal 2)

Input: one claim (a discrete sentence/clause from the summary's `<核心剧情>` or other tagged section), plus 1-3 raw stages selected by the orchestrator as most likely to contain support.

**System:** identical to P3a.

**User:**
```
以下是某次活动剧情摘要里的一条具体描述，以及若干原文片段。请判断这条描述是否在原文中找到直接支持。

描述：<<<CLAIM>>>

请使用以下格式输出：

<判断>
有依据 / 无依据 / 不确定
</判断>

<证据>
原文中支持该描述的最直接简短引用。无则写"无"。
</证据>

<说明>
不超过60字。如"无依据"，说明原文里相反的事实或为何无法佐证；如"不确定"，说明所提供片段不足以判断。
</说明>

【硬性要求】
- 严格使用简体中文。
- 不要在输出标签外添加内容。
- 如果描述只是对原文的合理时间/因果归纳，仍判"有依据"。
- 如果原文与描述存在事实矛盾，判"无依据"并在<说明>里点明矛盾。

原文片段：
<<<RAW_CHUNKS>>>
```

Required tags: `判断`, `证据`, `说明`.

> Older drafts of this file (pre-Codex review 07-consistency) had a single P3 prompt with `<遗漏>` + `<可疑描述>` batch tags. Replaced because the design now does per-item judgments and the orchestrator collapses same-stage items into one call rather than collapsing items into one schema.

---

## P4 — Audit a character wiki page (`scripts/kb_audit_wiki.py --target char`)

Same shape as P3, but for `<剧情高光>` claims in a char wiki. Each claim is checked against the raw stages where the char is grep-mentioned.

**User template (per-claim, called once per `<剧情高光>` bullet):**
```
以下是关于角色"<<<CHAR_NAME>>>"的一条剧情高光描述，以及若干段原文片段。请判断：

- 这条描述是否在原文中找到了直接支持（同一场景的台词、动作或叙述）？
- 如果找到了，请引用最直接的原文片段。
- 如果未找到，请说明：是描述完全没有依据，还是依据在我们提供的片段之外（不能确定）。

请使用以下格式输出：

<判断>
有依据 / 无依据 / 不确定
</判断>

<证据>
原文中的简短引用（如有）。无则写"无"。
</证据>

<说明>
不超过60字的解释。
</说明>

剧情高光：
<<<CLAIM>>>

原文片段：
<<<RAW_CHUNKS>>>
```

Required tags: `判断`, `证据`, `说明`.

---

## Blocklist for char-name grep (used in `indexer.py`, not an LLM prompt)

Names that are common nouns / nicknames / overly broad will be excluded from the grep pass that builds `char_to_events.json`. Initial list (extensible via `data/kb/blocklist.txt` if we later want it user-editable):

```
博士
干员
罗德岛
医疗
近卫
重装
狙击
术师
辅助
特种
先锋
源石
矿石病
```

These match commentary noise rather than character mentions. The list is small by intent — false positives are acceptable for retrieval recall; the LLM-derived 关键人物 list (P1 output) is the high-precision authority.

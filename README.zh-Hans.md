# arknights_lore_wiki_lib

> 🌐 [English](./README.md) · **简体中文**

把上游 `ArknightsGameData` 的 JSON 数据转换为：
1. 一个已发布的剧情百科（剧情概要、角色页面），位于 [`arknights_lore_wiki`](https://github.com/littlepangding/arknights_lore_wiki)。
2. 一个在原始游戏数据之上、面向 agent 的知识库（Q/A、审核）。

LLM 调用全部外包给 Gemini CLI / Gemini SDK / Claude CLI，编排型 agent 不会把自己的上下文窗口烧光。

## 三个仓库怎么拼起来

```
ArknightsGameData/        （上游 · 只读 —— 从游戏数据项目拉取）
        │
        ▼  由 libs/game_data.py 解析
本仓库（arknights_lore_wiki_lib/）
        │
        ▼  由 scripts/compile_website.py + 各 LLM 脚本写入
arknights_lore_wiki/      （已发布的百科：data/*.txt + docs/*.md）
```

到另外两个仓库的路径通过 `keys.json`（gitignored —— 见下面的"环境准备"）配置。

## 环境准备

```bash
git clone git@github.com:littlepangding/arknights_lore_wiki_lib.git
cd arknights_lore_wiki_lib
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

在 README 同级目录创建 `keys.json`：

```json
{
  "lore_wiki_path": "/abs/path/to/arknights_lore_wiki",
  "game_data_path": "/abs/path/to/ArknightsGameData",
  "llm_backend": "cli",
  "llm_model": "gemini-3-flash-preview",
  "genai_api_key": "..."
}
```

所有命令都从仓库根目录执行，使用项目自带的 venv（`keys.json` 是按相对路径加载的）：

```bash
.venv/bin/python -m scripts.<name>
```

## 你可以做什么

### 1. 上游数据更新后，更新剧情百科

把新内容拉进 `ArknightsGameData/` 后，端到端流程是：

> 找出新故事 → 与用户确认 → LLM 概括每个故事 → 抽出候选角色 → 与用户确认 → LLM 生成每个角色的百科页 → 编译站点 → 提交 & 发 PR。

不要手动拼步骤，从 Claude Code 里调用编排 skill 即可：

```
/update-lore-wiki
```

这个 skill 会在每次 LLM 调用前强制执行人工审核闸口与验证步骤。具体见 `.claude/skills/update-lore-wiki/SKILL.md`。

### 2. 构建知识库（无 LLM）

```bash
.venv/bin/python -m scripts.kb_build
```

读 `ArknightsGameData/`（如果 `kb_summaries/` 已烤好，也会一并读取以构建 event-scoped 的 `summary` 角色↔活动边层 —— 没有任何 LLM 调用），把章节级和角色级的确定性 chunk 写到 `data/kb/` 下（gitignored），再写一组 JSON 索引。对当前语料约 10 秒跑完。幂等；默认开启 prune。

### 3. 查询知识库

知识库为命令行消费而设计 —— 既可让 agent 用，也方便人手工查：

```bash
.venv/bin/python -m scripts.kb_query event list --family activity
.venv/bin/python -m scripts.kb_query event get act46side
.venv/bin/python -m scripts.kb_query event stages act46side          # 列出本活动的所有 <章节>；先挑一节再读
.venv/bin/python -m scripts.kb_query event chars act46side                       # 谁在这个活动里 —— 每行带 tier
.venv/bin/python -m scripts.kb_query event chars act46side --min-tier speaker    # 只列真正有台词的角色
.venv/bin/python -m scripts.kb_query event chars act46side --source deterministic # 只看 handbook-storyset 推导的精确链接
.venv/bin/python -m scripts.kb_query char resolve 阿米娅
.venv/bin/python -m scripts.kb_query char get char_002_amiya --section voice
.venv/bin/python -m scripts.kb_query char appearances char_002_amiya             # 该角色出现在每个 (event, stage) 的分层视图
.venv/bin/python -m scripts.kb_query char card char_002_amiya          # 确定性事实卡（基础档案 / 客观履历 / 皮肤 / 模组 / storyset）
.venv/bin/python -m scripts.kb_query grep 巨枭 --in summaries          # 在已烤的活动摘要里搜索（信号高）；也可 --in events|chars|all
```

默认输出 JSON；`--text` 在可行时返回原始 chunk。事实卡是确定性的（直接从 `character_table` + `handbook_info_table` 解析、每个字段标注来源），是检查百科页"基础信息"对不对的最便宜的核验锚点。

**角色↔章节边层共有三层**（`event chars` / `char appearances` 把它们合并；用 `--source` 指定层）：
- `deterministic` —— 精确的 handbook-storyset 链接（已验证 372/372）。Ground truth，永远通过任何 `--min-tier`。
- `participant` —— 从清洗过的 chunk 文本中推出来，带 `tier`：`speaker`（在该章节有 ≥1 句台词 —— "出现"的默认含义）、`named`（被叙述提及；ASCII 名字使用真实词边界，所以 `W` ⊄ `World`，单字 zh 名字需要 ≥2 次命中或同时出现在摘要里）、`mentioned`（仅一次顺带提及 —— 作为召回兜底保留，默认丢弃）。
- `summary` —— *event-scoped*（`stage_idx` 为 `null`）：来自已烤活动摘要的 `<关键人物>`，经别名索引解析得到。能捕捉到那些只被昵称 / 头衔提及、被 name-grep 漏掉的角色。

`--min-tier {speaker,named,mentioned}`（默认 `named`）只过滤 `participant` 边；`deterministic` 边永远穿透。

### 4. 烤制 LLM 摘要（`kb_summaries/`）

可选层 —— 小尺寸的中文摘要、入 git，作为导航辅助。两种粒度：每活动一份（默认）和每 `<章节>` 一份（`--stages`，输出到 `kb_summaries/stages/<event_id>/<NN>.md` —— 章节级检索层；约 1937 章节，全部单遍生成）。

```bash
.venv/bin/python -m scripts.kb_summarize --event story_12fce_set_1   # 单个活动
.venv/bin/python -m scripts.kb_summarize                              # 所有活动（消耗 token）
.venv/bin/python -m scripts.kb_summarize --stages                     # 所有章节（最大的一次烤制，消耗 token）
.venv/bin/python -m scripts.kb_summarize --stages --event act46side   # 仅烤某活动的所有章节
.venv/bin/python -m scripts.kb_summarize --llm cli --model gemini-3.1-pro-preview
.venv/bin/python -m scripts.kb_summarize --stages --estimate          # 干跑：还剩多少章节 / LLM 调用 / ≈ token
```

source-hash 缓存：对没变过的活动重跑是 no-op（不重复花 token）。真实运行时每个活动一行进度日志（`[i/N] <event_id>  done X/Y ev  ~tok_done/tok_total  elapsed  ETA ~…`），多小时的烤制不会沉默无声。如果模型返回的标签格式错乱，先做启发式修补（未闭合的末尾标签、全角括号、markdown 标签名等），修补失败才会重新提问；*每一次*模型响应的原文都会归档到 `llm_archive/<date>/` 下（gitignored —— 这些数据花钱了，而入 git 的 `.md` 只保留规范化的子集；`--no-archive` 可关闭，`--archive-dir` / `keys.json llm_archive_path` 可指定位置）。`--estimate` 不调用 LLM —— 仅打印计划运行的成本预测（要跑的活动数、单遍 vs 多遍、LLM 调用数、输入/输出/总字符 ≈ token），会响应 `--event` / `--force`。

## Skills（Claude Code）

仓库本地的 skill 在 `.claude/skills/` 下，把多步骤流程编排好，不需要你手动拼接。从 Claude Code 会话里调用时，可以直接报 skill 名（或单纯描述任务 —— 每个 `SKILL.md` 里写好的触发词会匹配上）：

| Skill | 它做什么 |
|---|---|
| `update-lore-wiki` | 游戏数据更新后的端到端 wiki 更新流程：找新故事 → 确认 → LLM 概括 → 发现候选角色 → 确认 → LLM 生成角色页 → 编译 → PR。在每次 LLM 调用前强制人工审核闸口。 |
| `refresh-kb` | 把 `data/kb/`（确定性构建）和 `kb_summaries/`（LLM 活动摘要）刷新到最新。同一个流程既能首次完整构建，也能后续增量刷新；`kb_build` 幂等、`kb_summarize` 命中 hash 缓存即跳过，所以对没变过的内容反复跑不花钱。 |

每个 skill 都在 `.claude/skills/` 下有自己的文件夹。读对应的 `SKILL.md` 可以看到准确的前置条件、流程和触发词。

## 目录布局

```
libs/
  bases.py        # 标签 schema、extract_tagged_contents、校验、get_value
  game_data.py    # 解析 ArknightsGameData JSON；clean_script
  llm_clients.py  # 统一的 Gemini CLI / SDK / Claude CLI 调度
  ui.py           # 把 data/*.txt 渲染成 docs/*.md（已发布的 wiki）
  kb/             # 知识库 package（paths, chunker, cards, indexer, participants, query, summarize）
scripts/
  find_new_stories.py + find_chars_in_new_stories.py  # 更新流程的辅助脚本
  get_story_wiki.py + get_char_wiki_v3.py             # LLM 生成器
  compile_website.py                                   # 渲染 wiki 文档
  kb_build.py + kb_query.py + kb_summarize.py         # KB CLI
tests/             # pytest，只用 mock —— CI 中没有真实 LLM 调用
docs/              # 设计历史（REQUIREMENTS、DESIGN、PROMPTS、DECISIONS、reviews/）
```

## 想深入了解

- **实现 / 评审者**：读 `docs/DESIGN.md`（架构 + 风险）和 `docs/DECISIONS.md`（重大决策日志）。
- **使用 KB 做 Q/A 或审核的 agent**：读 `docs/AGENTS_GUIDE.md`。
- **修改 prompt**：读 `docs/PROMPTS.md`。
- **可视化 schema**：[`docs/schema/`](./docs/schema/) —— 交互式概念地图 + 角色名检索流程图，通过 GitHub Pages 从 `/docs` 提供。启用 Pages 后即可访问 `https://littlepangding.github.io/arknights_lore_wiki_lib/schema/`。
- **AI 助手定位**：每个文件夹下的 `CLAUDE.md` 是更简洁、随时保持最新的运行手册。

## 约定

- 所有命令从仓库根目录运行。
- 始终使用 `.venv/bin/python` —— macOS 的系统 Python 会撞上 PEP 668。
- `keys.json` 已 gitignored，里面有 API key。绝不提交。
- 每个生成文件的写入都通过临时文件 + `os.replace` 原子完成 —— 半成品永远不会落到目标位置；重构时这一点是受力点。

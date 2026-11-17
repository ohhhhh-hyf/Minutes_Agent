# Visual Minutes（图文智能纪要）落地执行清单与架构规范

> **版本**：v1.2 (Lightweight Edition for Minutes Microservice)  
> **所属目录**：`demo/tasks/minutes/visual/`  
> **定位**：轻量级微服务框架下的图文智能纪要增强引擎。以流式直出并经最终逻辑清洗校验的不可变纪要正文为骨架，前置并发提取智能章节，中置抽取通用语义视觉 IR，基于确定性 HTML/CSS + Inline SVG 渲染，内联小艺品牌自包含资产，最终落盘为同级目录下的 `visual.html`。

---

## 目录
1. [需求与核心设计原则](#一需求与核心设计原则)
2. [端到端执行时序与生命周期](#二端到端执行时序与生命周期)
3. [目标目录结构规划](#三目标目录结构规划)
4. [核心子模块设计与技术规范](#四核心子模块设计与技术规范)
   - 4.1 环境变量与配置规范 (`.env` & `config.py`)
   - 4.2 极简 LLM 适配器 (`llm.py`)
   - 4.3 强类型语义 IR 数据模型 (`models/ir.py`)
   - 4.4 章节切分与原文检索 (`parsing/`)
   - 4.5 三 Agent 协同规划与抽取 (`agents/`)
   - 4.6 工业级校验、自愈与密度控制 (`validation/validator.py`)
   - 4.7 确定性渲染器、小艺品牌资产与合成器 (`rendering/`)
   - 4.8 智能章节前置并发模块 (`section_summary/`)
   - 4.9 主服务流程接入 (`agent.py` & `main.py`)
5. [七阶段落地执行任务卡 (Checklist)](#五七阶段落地执行任务卡-checklist)

---

## 一、需求与核心设计原则

### 1. 核心业务需求
1. **环境与核心参数全量收敛至 `.env`（严格 4 项）**：
   - 统一由 `VISUAL_MINUTES_ENABLED=on/off` 作为唯一开关，彻底剔除冗余的历史别名（不使用 `INTELLIGENT_MINUTES_ENABLED` 等别名）；
   - 关键工程调度参数（并发度 `MAX_EXTRACT_CONCURRENCY=4`、单章原文检索预算 `TRANSCRIPT_CHAR_BUDGET=5000`、全篇图表软上限 `MAX_SECTION_VISUALS=8`）全部在 `.env` 中统一定义与调优；
   - 视觉抽取大模型调用温度在代码中**直接写死为 0.2**，确保 JSON Schema 结构高度稳定与事实绝对保真，不开放外部环境变量干扰。
2. **小艺品牌视觉资产原生内聚**：
   - 用户提供的小艺品牌头像资产已物理落地至 `demo/tasks/minutes/visual/rendering/xiaoyi_icon.png`；
   - 配套生成 `xiaoyi_icon_uri.py`，将图片自动转换为 Base64 Data URI 常量，直接嵌入至输出的 `visual.html` 页脚与品牌徽标区；
   - 页面 100% 自包含（零外部图片网络依赖、零外链图裂风险），适配内网离线查看与本地分发。
3. **时序与流式直出保障**：
   - 当开关为 `on` 时，**智能章节（Section Summary）在第 0 秒与主纪要任务同时并发启动**；
   - 纪要文本实时流式向前端直出（SSE / NDJSON），前端首字时延（TTFT）与交互体验不受任何影响；
   - 纪要流式完毕后，执行最终层逻辑校验与格式清洗（`postprocess_minutes_markdown`），确保骨架绝对正确；
   - 随后基于【稳态清洗后纪要】+【现场原文转写】+【智能章节速览】，拉起视觉流水线生成图文页面。
4. **输出落盘与日志一致性**：
   - 产出的自包含交互式 HTML，保存在当前请求的同一输出目录下：`data/{user_id}/output/{request_id}/visual.html`；
   - 日志输出格式与根目录严格对齐（`[visual_minutes:start]`、`[section_summary:finish]`、`[visual_minutes:finish]` 等）。

### 2. 三大设计铁律
1. **正文不可变（Markdown Fidelity 100%）**：纪要正文是唯一事实核心，图表作为兄弟节点挂载于正文之后，绝对不改写、不删减 Markdown 原文任何字句。
2. **语义 IR 与渲染分离（Deterministic Rendering）**：模型**只输出强类型结构化 JSON**，排版完全由前端确定性 CSS 网格 + 内联 SVG 矢量箭头绘制，零多模态乱码，支持内网离线查看。
3. **单点失败严格隔离（Graceful Fallback）**：单图报错自动丢弃，开篇大看板自动规则保底，绝不拖垮主纪要直出。

---

## 二、端到端执行时序与生命周期

```text
第 0 秒：/api/agent/v1 或 /api/agent/v1/stream 接收到纪要请求
  │
  ├─ 检查 .env 中的 VISUAL_MINUTES_ENABLED 开关
  │   │
  │   ├─ [on]：立即以 asyncio.create_task 后台并发拉起【智能章节任务】
  │   │       (基于逐字稿提炼 3~8 个时间轴大章节，耗时约 5~8s，静默在后台跑完)
  │   │
  │   └─ [off]：跳过智能章节与后续图文流水线
  │
  └─ [主任务并行]：主纪要流式生成（模板分类 -> 议程对齐 -> 实时 SSE 文本直出，耗时约 15~30s）
       │
       ▼
主纪要流式结束（约第 20 秒）
  ├─ 1. 最终层逻辑校验与清洗：调用 postprocess_minutes_markdown 生成唯一不可变稳态正文；
  ├─ 2. 纪要落盘：保存至 data/{user_id}/output/{request_id}/minutes.md；
  ├─ 3. 收集智能章节结果：await summary_task（耗时几乎 0s，若异常秒级走规则时间窗兜底）；
  ├─ 4. 拉起图文增强流水线：
  │      ├─ Section Parser：解析 Markdown AST，标注稳定 section_id (s1, s2...)
  │      ├─ Transcript Retriever：按发言段落 Bigram 检索章节专属原文 (<=TRANSCRIPT_CHAR_BUDGET)
  │      ├─ Section Planner (Agent 1)：审视章节元数据，决定是否出图（默认 none）
  │      ├─ Visual Extractor (Agent 2) & Master Planner (Agent 3)：按 MAX_EXTRACT_CONCURRENCY 并发抽取
  │      ├─ IR Validator：执行表格补全、流程图去孤立边、树父节点重置及 MAX_SECTION_VISUALS 软上限控制
  │      └─ Document Compositor：HTML/CSS + Inline SVG 确定性渲染，嵌入小艺品牌 Base64 图标，装配飞书同款 TOC 目录导航
  ├─ 5. 图文落盘：渲染生成自包含独立 HTML，保存在同一目录下的 visual.html；
  └─ 6. 事件收尾：发送 type: done 帧，携带生成结果与遥测指标，记录与根目录一致的规范日志。
```

---

## 三、目标目录结构规划

模块完全收敛并内聚在 `demo/tasks/minutes/visual/`：

```text
demo/tasks/minutes/visual/
├── __init__.py                  # 导出 agenerate_visual_minutes, generate_visual_minutes
├── pipeline.py                  # 顶层调度流水线 (build_visual_ir, agenerate_visual_minutes)
├── config.py                    # 集中从 .env 读取 4 项配置，温度固定 0.2
├── llm.py                       # 极简适配层：对接 demo 的 infra.llm.LLMClient 与 chat_json
├── models/
│   ├── __init__.py
│   └── ir.py                    # 5 大原语 (Timeline/Table/Flow/Tree/Stats) + Master 看板 IR
├── parsing/
│   ├── __init__.py
│   ├── parser.py                # Markdown AST 章节切分器 (生成 SectionNode 树)
│   └── retrieval.py             # 章节专属发言原文切块检索器 (Bigram 加权评分)
├── agents/
│   ├── __init__.py
│   ├── planner.py               # Agent 1: Section Planner 章节必要性规划
│   ├── extractor.py             # Agent 2: Visual Extractor 单章节结构化数据抽取
│   ├── master.py                # Agent 3: Master Visual Planner 置顶大看板与规则兜底
│   └── prompts.py               # 纯净契约 Prompt 定义
├── validation/
│   ├── __init__.py
│   └── validator.py             # 数据校验与自愈对齐修复器 (表格补全/死边清理/树修复/密度截断)
├── rendering/
│   ├── __init__.py
│   ├── xiaoyi_icon.png          # [已就绪] 小艺高清品牌头像资产文件 (72KB)
│   ├── xiaoyi_icon_uri.py       # [已就绪] 自动生成的 Base64 内联 Data URI 常量模块
│   ├── compositor.py            # 页面合成器 (TOC 树构建、Master 置顶、正文兄弟挂载、小艺页脚免责声明)
│   └── renderer/                # 确定性 HTML/CSS + SVG 渲染器集合
│       ├── base.py              # Corporate Clean 设计系统 Tokens 与通用 CSS (含小艺品牌样式)
│       ├── master.py            # 开篇全景看板渲染
│       ├── timeline.py          # 时序轴渲染
│       ├── table.py             # 矩阵表格渲染
│       ├── flow.py              # 流程连线与步骤箭头渲染
│       ├── tree.py              # 树状层级拓扑渲染
│       └── stats.py             # 量化指标卡片渲染
└── section_summary/             # 智能章节附录模块
    ├── __init__.py              # 导出 agenerate_section_summary
    ├── agent.py                 # SectionSummaryAgent 异步提炼 (3~8 个大章节)
    ├── parser.py                # 逐字稿正则时间戳解析与分块清洗
    ├── prompts.py               # 纯 JSON 输出契约提示词
    ├── validation.py            # 结构提取与时间戳单调递增校验
    ├── fallback.py              # 纯规则时间静默间隔/固定时间窗保底
    └── renderer.py              # 确定性 Markdown 引用块渲染器
```

---

## 四、核心子模块设计与技术规范

### 4.1 环境变量与配置规范 (`.env` & `config.py`)

#### (1) `demo/.env` 统一定义（严格 4 项，无兼容别名）
```bash
# -------------------------------------------------------------
# 图文智能纪要配置 (Visual Minutes)
# -------------------------------------------------------------
VISUAL_MINUTES_ENABLED=on          # 图文纪要主开关 (on 开启, off 关闭)
MAX_EXTRACT_CONCURRENCY=4          # 视觉数据抽取并发上限
TRANSCRIPT_CHAR_BUDGET=5000        # 单章节原文检索字符预算
MAX_SECTION_VISUALS=8              # 全文正文图表软上限
```

#### (2) `config.py` 解析逻辑（温度写死 0.2）
```python
import os

# 1. 唯一主开关 (默认 off)
VISUAL_MINUTES_ENABLED: bool = os.getenv("VISUAL_MINUTES_ENABLED", "off").strip().lower() == "on"

# 2. 抽取并发限制 (默认 4)
MAX_EXTRACT_CONCURRENCY: int = int(os.getenv("MAX_EXTRACT_CONCURRENCY", "4").strip())

# 3. 单章节原文检索字符上限 (默认 5000 字)
TRANSCRIPT_CHAR_BUDGET: int = int(os.getenv("TRANSCRIPT_CHAR_BUDGET", "5000").strip())

# 4. 全文正文图表软上限 (默认 8 张)
MAX_SECTION_VISUALS: int = int(os.getenv("MAX_SECTION_VISUALS", "8").strip())

# 5. 视觉大模型抽取温度直接在代码写死为 0.2，确保 JSON 结构稳定与事实保真
DEFAULT_VISUAL_TEMPERATURE: float = 0.2
```

---

### 4.2 极简 LLM 适配器 (`llm.py`)
直接基于 `demo/infra/llm/client.py` 封装，摆脱外部冗余依赖：
- 实现 `VisualLLM` 类：内部持有 `LLMClient` 实例；
- 实现 `json_call(system_prompt, user_prompt, ...)`：调用 `client.chat_json(...)` 或通过正则提取代码块并解析 JSON，温度默认取 `DEFAULT_VISUAL_TEMPERATURE = 0.2`。

---

### 4.3 强类型语义 IR 数据模型 (`models/ir.py`)
基于 Pydantic 2.x 定义：
1. **`TimelineVisual`**：`time_label`、`title`、`detail`、`evidence`；
2. **`TableVisual`**：`columns`、`rows`（含单元格列表与证据）；
3. **`FlowVisual`**：`nodes`（`node_id`, `title`, `detail`）与 `edges`（`source`, `target`, `label`）；
4. **`TreeVisual`**：`nodes`（`node_id`, `parent_id`, `title`, `detail`）；
5. **`StatsVisual`**：`metrics`（`label`, `value`）；
6. **`MasterVisual`**：`headline` + `widgets`（包含 `CardsVisual` 等多组件流）。

---

### 4.4 章节切分与原文检索 (`parsing/`)
- `parser.py`：使用 `markdown_it` 将正文解析为 AST，自适应合并或提升层级，产出带有全局稳定 `section_id`（s1, s2...）的 `SectionNode` 列表；
- `retrieval.py`：对逐字稿按发言人换行切分，基于 Section 纯文本计算词频与 Bigram 相似度，提取得分最高的发言片段并按原时序重新拼接（预算 $\le \text{TRANSCRIPT\_CHAR\_BUDGET}$）。

---

### 4.5 三 Agent 协同规划与抽取 (`agents/`)
- **Agent 1: SectionPlanner** (`planner.py`)：审视全部章节元数据，默认输出 `none`，仅在满足因果/对比/量化等特征时给出对应图原语决策；
- **Agent 2: VisualExtractor** (`extractor.py`)：逐章抽取 5 大原语数据，受并发信号量 `asyncio.Semaphore(MAX_EXTRACT_CONCURRENCY)` 保护；
- **Agent 3: MasterPlanner** (`master.py`)：提炼全篇开篇大看板；内建 `fallback_master(parsed)` 规则保底（从一级章节自动提取议题立柱卡片，模型调用失败时秒级兜底）。

---

### 4.6 工业级校验、自愈与密度控制 (`validation/validator.py`)
- **最低门槛过滤**：表格 $\ge 2\times 2$、流程图 $\ge 3$ 节点 1 边、时间轴 $\ge 3$ 事件，不合规直接静默丢弃；
- **自动对齐纠错（Auto-Healing）**：
  - 表格行单元格数量少于列数自动补 `""`，多于列数自动截断；
  - 流程图自动剔除指向不存在节点的孤立悬空边；
  - 树状图若找不到 `parent_id` 自动提升为根节点；
- **密度控制**：正文图表超过 `MAX_SECTION_VISUALS`（8 张）时，按规划阶段的置信度/必要性得分排序，保留最重要的前 8 张。

---

### 4.7 确定性渲染器、小艺品牌资产与合成器 (`rendering/`)
- **小艺品牌头像资产**：
  - 源文件：`tasks/minutes/visual/rendering/xiaoyi_icon.png`；
  - 自动编译为内联 Data URI 模块：`tasks/minutes/visual/rendering/xiaoyi_icon_uri.py`，对外导出 `XIAOYI_ICON_DATA_URI = "data:image/png;base64,..."`；
  - 在页面尾部免责声明中优雅呈现：
    ```html
    <footer class="vm-footer">
      <div class="vm-footer-content">
        <img class="vm-footer-icon" src="{XIAOYI_ICON_DATA_URI}" alt="小艺慧记" />
        <span class="vm-footer-text">本纪要由小艺慧记 AI 自动生成，图文可视化内容仅供参考。</span>
      </div>
    </footer>
    ```
- **Corporate Clean 设计系统** (`base.py`)：纯内置企业设计规范 Tokens、自包含 CSS 栅格系统与响应式布局；
- **渲染器集合** (`renderer/*.py`)：纯 HTML5 + Inline SVG 渲染器，支持步骤连线箭头、状态图标徽标；
- **页面合成器** (`compositor.py`)：
  - 原始 Markdown 正文原封不动转为 HTML；
  - 对应图表以兄弟节点插入在章节末尾；
  - 自动抽取各级标题生成飞书同款左侧 TOC 目录导航树（含滚动监听与平滑跳转）；
  - 顶端注入 Master 全景看板，末尾挂载智能章节附录，底部呈现小艺免责声明，达成 100% 离线自包含。

---

### 4.8 智能章节前置并发模块 (`section_summary/`)
- `SectionSummaryAgent`：利用逐字稿提炼 3~8 个时间轴大章节速览（`time_start`, `title`, `key_words`, `section_summary`）；
- `fallback.py`：大模型故障时，按静默停顿（60s）或固定时间窗（10分钟）纯规则切块兜底，确保 100% 产出；
- `renderer.py`：渲染为标准 Markdown 引用块并挂载进 HTML 尾部。

---

### 4.9 主服务流程接入 (`agent.py` & `main.py`)
- 在 `demo/tasks/minutes/agent.py` 的 `stream_minutes_agent` 中：
  1. 第 0 秒：检查 `VISUAL_MINUTES_ENABLED` 开关，若开启则拉起 `summary_task = asyncio.create_task(agenerate_section_summary(transcript))`；
  2. 正常流式生成正文，调用 `postprocess_minutes_markdown` 清洗；
  3. `await summary_task` 获取附录；
  4. 调用 `agenerate_visual_minutes(md_text, transcript, output_path=visual_html_path, section_summary_markdown=sec_md)`；
  5. 记录日志并在 `type: done` 事件中下发 `visual_file` 字段。

---

## 五、七阶段落地执行任务卡 (Checklist)

- [x] **阶段一：数据模型与基础配置**
  - [x] 在 `demo/.env` 声明 4 项配置（`VISUAL_MINUTES_ENABLED`, `MAX_EXTRACT_CONCURRENCY`, `TRANSCRIPT_CHAR_BUDGET`, `MAX_SECTION_VISUALS`）；
  - [x] 新建 `demo/tasks/minutes/visual/` 目录结构；
  - [x] 编写 `config.py`，从环境变量读取上述 4 项配置，温度固定 0.2；
  - [x] 编写 `models/ir.py`（5 大通用原语 + Master 看板 Pydantic 模型）；
  - [x] 编写 `llm.py`（适配 `demo.infra.llm.LLMClient`，默认温度 0.2）。

- [x] **阶段二：切分、检索与数据校验器**
  - [x] 移植 `parsing/parser.py`（Markdown AST 解析与 SectionNode 提取）；
  - [x] 移植 `parsing/retrieval.py`（Bigram 原文相关度加权检索，受 `TRANSCRIPT_CHAR_BUDGET` 控制）；
  - [x] 移植 `validation/validator.py`（数据完整性校验、表格对齐自愈、受 `MAX_SECTION_VISUALS` 限制）。

- [x] **阶段三：确定性渲染器与 Compositor**
  - [x] **部署品牌视觉资产**：`tasks/minutes/visual/rendering/xiaoyi_icon.png` 放置就绪；
  - [x] **编译内联 URI 模块**：自动生成 `tasks/minutes/visual/rendering/xiaoyi_icon_uri.py`，导出 `XIAOYI_ICON_DATA_URI`；
  - [x] 移植 `rendering/renderer/base.py`（Corporate Clean 纯内置样式表，包含小艺免责声明与徽标样式）；
  - [x] 移植 `rendering/renderer/*.py`（timeline, table, flow, tree, stats, master）；
  - [x] 移植 `rendering/compositor.py`（正文兄弟节点挂载、左侧 TOC 目录导航树构建、注入小艺免责声明）。

- [x] **阶段四：三 Agent 规划与抽取体系**
  - [x] 移植 `agents/prompts.py` 提示词契约；
  - [x] 移植 `agents/planner.py`（Agent 1: 章节必要性规划）；
  - [x] 移植 `agents/extractor.py`（Agent 2: 单章节结构化数据抽取，受 `MAX_EXTRACT_CONCURRENCY` 控制）；
  - [x] 移植 `agents/master.py`（Agent 3: 开篇看板规划与 `fallback_master` 规则保底）；
  - [x] 封装顶层流水线 `pipeline.py`。

- [x] **阶段五：智能章节（Section Summary）并发模块**
  - [x] 移植 `section_summary/parser.py`（逐字稿时间戳解析）；
  - [x] 移植 `section_summary/prompts.py` 与 `validation.py`；
  - [x] 移植 `section_summary/fallback.py`（纯规则时间窗切分保底）；
  - [x] 移植 `section_summary/renderer.py`；
  - [x] 封装 `section_summary/agent.py` 与 `__init__.py`。

- [x] **阶段六：主服务与接口层串联**
  - [x] 修改 `demo/tasks/minutes/agent.py`（第 0 秒根据 `VISUAL_MINUTES_ENABLED` 拉起智能章节、末尾驱动图文流水线并落盘 `visual.html`）；
  - [x] 在 `demo/main.py` 完善返回载荷与错误容灾（`done` 事件载荷下发 `visual_file`）。

- [x] **阶段七：端到端验证与全量回归**
  - [x] 编写分阶测试与端到端测试（`test_visual_step1.py`, `test_visual_step2.py`, `test_visual_step3.py`, `test_visual_minutes_integration.py`，覆盖模型解析、渲染合成与 Fallback 兜底）；
  - [x] 验证 `visual.html` 离线打开与交互效果（包含小艺 Base64 图标展示、免责声明、目录点击平滑滚动、流程图箭头渲染）；
  - [x] 运行全量测试套件确保 100% 通过无回退。

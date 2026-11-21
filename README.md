# 舆情分析系统 · 多 Agent 交叉研判

基于 **FastAPI + LangGraph** 的多 Agent 舆情研判系统。私域检索 Agent 与公域检索 Agent 并行产出同一组固定章节，再由研判 Agent **逐章节交叉裁决**，最终汇总裁决与双份报告生成可交付的分析报告。

## 设计思路

单一模型一次生成整篇报告，问题是：结论没有交叉验证，冲突被悄悄抹平。本项目让两个 Agent **各看各的数据源、各写各的章节**，由第三个 Agent 专门负责比对与裁决，冲突点被显式保留在结构化结果里。

## 四个角色

| 角色 | 职责 | 数据来源 |
| --- | --- | --- |
| `insight` 私域检索 | 查本地社媒库，输出公众情绪、争议点、平台差异 | MySQL + Milvus |
| `media` 公域检索 | 查公开报道，分析信源、报道角度、传播路径 | 外部搜索（三种工具） |
| `host` 研判 | 逐章节裁决双源结论，输出结构化裁决 | 订阅双方输出 |
| `report` 报告引擎 | 汇总裁决 + 双份报告，生成 Markdown / HTML | 落盘产物 |

各角色模型配置独立，可按任务特点异构路由。

## 三张 LangGraph

```
insight 图
  retrieve_evidence -> rerank_evidence -> route_section_evidence
  -> prepare_sections -> summarize_sections <循环 cursor>
  -> generate_agent_report -> persist_agent_report

media 图
  plan_search -> search -> summarize_sections <循环>
  -> generate_agent_report -> persist_agent_report

host 图（按章节增量触发，不阻塞主链路）
  collect_agent_output ->(双源齐备?)-> generate_section_judgement
  -> apply_section_judgement ->(全部章节判完?)-> save_judgements
```

## 五个固定章节

| key | 标题 |
| --- | --- |
| `background_overview` | 事件背景与概览 |
| `heat_and_spread` | 舆情热度与传播 |
| `sentiment_and_opinion` | 公众情感与观点 |
| `platform_and_group_diff` | 平台与群体差异 |
| `deep_causes_and_impact` | 深层原因与影响 |

每章都有**两份独立的写作指导**：私域侧限定「仅基于社媒样本、不将用户表达视为已核实事实」；公域侧要求区分信源陈述、媒体转述与未核实信息。这是防幻觉的硬约束，而不是一句「不要编造」。

## 核心设计

1. **私域双通道召回** — MySQL 侧 jieba 抽 2–4 字关键词做多词模糊查询；Milvus 侧 BGE-M3 稠密 + 稀疏双路检索（`RRFRanker` 融合），按 `published_at` 时间窗过滤。两路并发后按稳定 `doc_id` 去重合并。
2. **热度加权重排** — 舆情不能只看语义相似度。重排分 = `召回分 x 0.6 + 归一化热度 x 0.4`，召回分内再按向量 / 数据库各 `0.5` 加权，让「既相关又有讨论度」的证据优先进入章节。
3. **章节证据路由双模** — 规则模式按关键词首个命中分配；语义模式用轻量中文向量模型对证据与「章节标题 + 关键词」做余弦 argmax 分配，可按配置开关切换。
4. **公域搜索按章节选工具** — LLM 为每章生成搜索计划：综合检索 / 政府央媒溯源 / 近一周社媒实时，三种工具对应不同信源权威性与时效性，再用 `asyncio.gather` 并发多关键词检索、`zip_longest` 轮询合并去重。
5. **章节级增量研判** — 不等两个 Agent 全部完成，按 `section_key` 累积双方输出，同一章节凑齐即触发裁决。监听器用 `asyncio.Queue` 单消费者串行消费，保证配对状态不被并发改写；裁决经 `json_schema` 结构化约束，并强制回填 `section_key` 防止模型错章。
6. **事件驱动松耦合** — 自研事件总线抽象 5 类事件（章节就绪 / 角色进度 / 结果 / 异常 / 讨论消息），单订阅者异常不影响其他订阅者；SSE 按 `task_id` 维护连接队列池并在断开时清理。

## 接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/research` | 提交研究任务，返回 `task_id` |
| `GET /api/events/stream?task_id=` | SSE 推送进度与结果 |
| `GET /api/research/results?task_id=` | 读取各角色报告 |
| `GET /api/host/discussion?task_id=` | 读取讨论区记录 |
| `GET /api/report/status?task_id=` | 检查三份产物是否齐备 |
| `POST /api/report/generate` | 触发生成报告，返回 `generation_id` |
| `GET /api/report/result/{generation_id}` | HTML 在线预览 |
| `GET /api/report/download/{generation_id}/{html\|md}` | 下载报告 |

**两段式流程**：先研究产出 `report.md` / `judgements.json`，齐备后再单独触发生成报告 —— 研究报告失败或想重新生成时，不必重跑整条研究链路。

## 目录结构

```
sentiment_analysis/
├── app/
│   ├── routers/      rest/（research/host/report/system）+ sse/
│   ├── services/     research / report / host / sse / lifecycle / system
│   ├── schemas/      请求响应模型
│   ├── exceptions/   全局异常处理
│   ├── app.py        应用组装（lifespan 注册监听器）
│   └── dependencies.py
├── engines/
│   ├── contract/     agent_role / events / evidence / judgement / section_definitions / settings
│   ├── common/       llm / retries / section_summary / report_generation / research_tasks ...
│   ├── insight_agent/  nodes/ + tools/（db、vector）
│   ├── media_agent/    nodes/ + web_search/（providers）
│   ├── host_agent/     section_pair_store / section_ready_listener / section_judge
│   ├── report_engine/  engine / renderer / models
│   ├── orchestration/  orchestrator
│   └── prompts/        host / insight / media / report / shared
└── main.py
```

## 配置

仓库不含 `.env`（已 gitignore），可复制 `.env.example` 后填写：

```dotenv
# 私域检索角色
INSIGHT_AGENT_API_KEY=
INSIGHT_AGENT_BASE_URL=
INSIGHT_AGENT_MODEL_NAME=

# 公域检索角色
MEDIA_AGENT_API_KEY=
MEDIA_AGENT_BASE_URL=
MEDIA_AGENT_MODEL_NAME=

# 研判角色
HOST_AGENT_API_KEY=
HOST_AGENT_BASE_URL=
HOST_AGENT_MODEL_NAME=

# 报告引擎
REPORT_ENGINE_API_KEY=
REPORT_ENGINE_BASE_URL=
REPORT_ENGINE_MODEL_NAME=

# 数据库
DB_HOST=  DB_PORT=  DB_USER=  DB_PASSWORD=  DB_NAME=

# 搜索服务
ANSPIRE_API_KEY=  ANSPIRE_BASE_URL=

# 向量检索（可选开关）
INSIGHT_VECTOR_ENABLED=true
MILVUS_URI=http://localhost:19530
MILVUS_DB_NAME=default
MILVUS_INSIGHT_COLLECTION=insight_evidence
INSIGHT_EMBEDDING_MODEL=BAAI/bge-m3
INSIGHT_DENSE_DIM=1024
INSIGHT_VECTOR_FILTER_DAYS=365
INSIGHT_SEMANTIC_ROUTING_ENABLED=true
INSIGHT_SEMANTIC_ROUTING_MODEL=BAAI/bge-small-zh-v1.5
```

## 启动

```bash
uv sync
python -m sentiment_analysis.main      # 默认 :5000，见 settings 中 HOST / PORT
```

报告产物目录：

```
var/reports/{task_id}/
├── insight/report.md
├── media/report.md
├── host/judgements.json
└── report/generation_xxx.{md,html}
```

## 说明

- 个人项目，用于验证多 Agent 并行研究与交叉裁决的工程实现，非公司线上系统。
- 事件总线为**进程内**发布订阅，适合单实例部署；多副本或需消息持久化时应替换为 Redis Pub/Sub、RabbitMQ 或 Kafka。
- **已知局限**
  - 任务状态与连接队列均在进程内存，重启即丢；
  - SSE 无断线重放缓冲；
  - 5 个章节为代码内固定定义，尚未抽成可配置模板；
  - 暂无自动评测集。
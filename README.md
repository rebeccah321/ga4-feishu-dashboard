# Seeed 官网流量分析管道

基于 [traffic-analytics skill](../Desktop/traffic-analytics/SKILL.md) 规范搭建的 GA4 流量数据管道。

## 架构

```
GA4 API (property 502086217)
    ↓ 每日拉取
GitHub (CSV/JSON 数据 + GitHub Pages dashboard)
    ↓ 每周同步
飞书多维表格 (Bitable) → 可视化呈现
```

## 核心修正（vs 之前 ga4-feishu-dashboard 项目）

| 问题 | 之前 | 现在 |
|------|------|------|
| GA4 属性 | 258704823（商城站） | **502086217**（企业官网三语言站） |
| CN 双路径 | 未合并 | `/slug` + `/slug-zh-hans` 自动相加 |
| 垃圾 URL | 未过滤 | 剔除含 `{`/`}` 的路径 |
| 语言映射 | 无 | hostName → EN/CN/JP |
| 更新频率 | 周更 | **日更**（GitHub）+ 周更（飞书） |
| 飞书同步 | 手动 | GitHub Actions 自动推送 |

## 标准化字段格式

### 日级明细 fact table（`data/normalized/`）
```
date, week_start, host_name, lang, page_path, page_title,
channel_group, device_category, screen_page_views, active_users,
sessions, engagement_rate, bounce_rate, avg_engagement_seconds,
key_events, total_revenue, pulled_at
```

### 衍生分析表（`data/analysis/`）
- `solution_summary.csv` — 方案 × 语言（PV、用户、人均时长）
- `funnel_summary.csv` — category → list → lora → solutions 各层 × 语言
- `ratio_summary.csv` — 方案区占全站比例 × 语言
- `channel_summary.csv` — 方案页流量来源渠道 × 语言

## 本地运行

```bash
# Mock 测试（无需网络）
./scripts/run_pipeline.sh --mock 7

# 真实数据（需 gcloud ADC 已登录）
./scripts/run_pipeline.sh 28

# 仅拉取
python3 scripts/ga4_fetch.py --days 28

# 仅渲染
python3 scripts/render_dashboard.py
```

## GitHub Actions

### 日更（`daily-sync.yml`）
- 每天 02:00 Asia/Shanghai 自动拉取 GA4 数据
- 提交 CSV/JSON 到仓库
- 部署 dashboard 到 GitHub Pages

### 飞书周更（`weekly-feishu-sync.yml`）
- `Weekly GA4 Tables` 成功后自动触发（或每周一 09:30 兜底）
- 自动识别并写入已有的 `01_方案增长总览`、`02_单方案流量明细`、`03_转化漏斗`
- 不创建、不重命名表；按 `周次`/`周次+方案名称` upsert
- 01/02/05 读取 v3 口径对外表（见下节），03 沿用 `data/analysis/转换漏斗.csv`

### 周报表与飞书拉取（`weekly-tables.yml`）
- 每周一 09:00 Asia/Shanghai 拉取 42 天数据并生成三张周度 CSV
- 追加生成 v3 方案区口径：`generate_tables_v3.py --weeks 20` → `merge_leads.py` → `build_v3_tables.py --no-partial`
  （顺序不能颠倒：先拉线索再合成对外表；输出 `data/analysis_v3/`，`01_方案增长总览.csv`、`02_单方案流量明细.csv`、`04_参考设计Top3.csv` 为飞书 01/02/05 表数据源）
- 导出 `dashboard/data/weekly/weekly_tables.json`（完整三表）和 `weekly_summary.json`（单条方案增长汇总）
- 提交到 GitHub Pages，供飞书多维表格自动化每周一 09:10 拉取

## v3 方案区口径（2026-10 起）

脚本链路（本仓库 `scripts/`）：

| 脚本 | 作用 |
|------|------|
| `generate_tables_v3.py` | 方案区整树周表（`/solutions/`、`/solutions-by-scenarios`、旧目录树）：01 方案区总览 / 02 流量类别 / 03 页面流量明细 → `data/analysis_v3/` |
| `partial_week_v3.py` | “本周至今”部分周表（环比=上周同期）→ `data/analysis_v3_partial/` |
| `merge_leads.py` | LeadApi 线索回填：只统计 23 个方案页（与 `generate_tables_v3.CORE_SLUGS` 同源）的去重 submission_id → `data/leads/leads_weekly.csv` |
| `build_v3_tables.py` | 合成对外表 → `01_方案增长总览.csv`、`02_单方案流量明细.csv`（按实际方案数：方案首次出现流量那一周起行 + `页面类型` + `leads` 列）、`04_参考设计Top3.csv`（每周流量前三；`--no-partial` 供 CI 只出完整周） |
| `sync_weekly_feishu.py` | 飞书同步：01/02/05 读 v3 对外表，03 沿用旧漏斗表 |
| `migrate_feishu_fields.py` | 飞书字段迁移（幂等）：默认只改名/改类型/补字段；`--delete-fields` 才删除废弃列 |

口径要点：
- 浏览 PV = 页级求和（不去重，含参考设计/场景案例/旧目录页/其他页面）；
- 独立访客/会话 = 整树逐周去重（无 date 维度的 totals 查询）；
- 404 死页（多语言 Not Found 标题）归入“其他页面”，只计入总数不列明细；
- CTA = `click_solution_link` + `click_contact_expert`。
- 截止日期/最新周次一律是**当周最后一天（周日）**：哪怕拉取日只是当周周一/周五、数据只到当天，日期列也写当周周日（如 10/23 拉取 → 2026-10-25）；是否完整由“数据状态 = 本周（未完整）”表达（`partial_week_v3.py` 输出周日，`build_v3_tables.py` 以“最后一周是否来自 partial 目录”判定未完整，GA4 补查截止日取今天）；
- 02 表按实际情况出行：方案页（共 23 个候选 = **15 个场景页 + 7 个技术页 + 1 个入口页**，与站内 `solutionMeta.docType` 对齐）在**首次出现流量那一周**才起行，之后每周一行，
  尚未上线/尚未出现的方案不出行（历史行数 12 → 13 → 14 → 15 → 21 → 22 → 23）；行数会变，改结构后用 `sync_weekly_feishu.py --prune` 清掉飞书里多出来的旧行；
- 02 表 `页面类型` 列（场景页/技术页/入口页）：场景页与技术页是站点两层结构（技术页按 by-tech 聚合、向下链接场景页），不是重复页；入口页指 `/solutions/sensecraft-solution` 这类品牌/引导页；
- 页面改名按周处理：改名**发生的那一周**起用新名，之前的周保留旧名（`generate_tables_v3.SLUG_RENAME`）。当前记录：`voicecollectionanalysis` 语音采集分析 → **门店客服质检**、`conversational-voice-ai` 对话式语音AI → **离线语音控制**，均自 W41（2026-10-11）起（依据 GA4 逐日 pageTitle 实测）；
- 名称与网页对齐（非按周改名，全周统一）：`in-person-conversation-intelligence` → **智能工牌会话分析**（站内中文标题"智能工牌语音转写与会话分析"，reSpeaker Clip 智能工牌；旧表写的"现场对话智能"已作为别名兼容）；
- 飞书 `03_转化漏斗` 的方案名称与 02 同源（`sync_weekly_feishu.py` 直接取 `generate_tables_v3.solution_name(slug, week)`），因此两张表的名称与改名周完全一致；旧写法（语音采集与分析 / 智慧农业传感 / 智能畜牧养殖 / 楼宇能源改造）已登记为别名，只用于 upsert 键匹配；
- 参考设计等其他页面只计入 01 总量、不单独列为方案，其每周流量前三列在 04/05 表；`leads` 只统计这些方案页来源的表单提交。

飞书字段名变化（2026-10）：

| 表 | 旧字段名 | 新字段名 |
|----|---------|---------|
| 01 | 方案页独立访客数 | 方案区独立访客数 |
| 01 | Solution页面总访问量 | 方案区浏览PV |
| 01 | Solution页面周环比 | 浏览PV周环比(%) |
| 02 | 落地页访问量 | 方案页浏览PV |
| 02 | Session | 会话数 |
| 02 | 平均停留时长（秒） | 平均停留时长(秒) |
| 02 | 周环比 | 周环比(%) |
| 02 | leads | 已恢复每周更新：23 个方案页的 LeadApi 表单提交（此前"历史全 0"为误判，数据一直存在） |
| 02 | （2026-10 新增列）页面类型 | 场景页 / 技术页 / 入口页（文本字段；`migrate_feishu_fields.py` 自动补字段） |

## 需要配置的 GitHub Secrets

| Secret | 用途 |
|--------|------|
| `GA4_SERVICE_ACCOUNT_JSON` | 服务账号 JSON（CI 认证） |
| `GA4_SERVICE_ACCOUNT` | 服务账号 JSON（`weekly-tables.yml` 使用） |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |
| `FEISHU_BITABLE_APP_TOKEN` | 飞书 Base token（新版按表名自动发现） |
| `FEISHU_BITABLE_TABLE_ID` | 旧版 `scripts/sync_feishu.py` 使用，可留空 |

## 飞书现有表字段对齐

| 飞书表 | upsert key | 数据来源 |
|--------|-----------|---------|
| `01_方案增长总览` | `周次` | `data/analysis_v3/01_方案增长总览.csv`（v3 口径） |
| `02_单方案流量明细` | `最新周次 + 方案名称` | `data/analysis_v3/02_单方案流量明细.csv`（v3 口径） |
| `03_转化漏斗` | `最新周次 + 方案名称` | `data/analysis/转换漏斗.csv` |
| `05_参考设计Top3` | `最新周次 + 排名` | `data/analysis_v3/04_参考设计Top3.csv` |

> 05 表字段：最新周次 / 截止日期 / 排名 / 页面名称（统一短中文名，按路径登记在 `REF_NAME_CN`）/ 浏览PV / 数据状态（`完整周` 或 `本周（未完整）`）。
> 表里的“按周分组 + 排序”是飞书视图设置，**开放接口不支持**，需在表内点一次：分组→`最新周次`，排序→`最新周次`降序 + `排名`升序。

> 编号互不相干：本地 `data/analysis_v3/NN_*.csv` 是流水线内部编号，与飞书表编号不是一回事。
> 例如本地 `03_页面流量明细.csv` 只是生成 01/02/04 的页面级原料（方案页全列 + 参考设计 Top3 + “其余页面”合计），**不上飞书**；
> 飞书 `03_转化漏斗` 读的是老口径 `data/analysis/转换漏斗.csv`，飞书 `04_社媒内容表现` 不由本仓库脚本写入。

> 注：飞书应用当前无“新建数据表”权限（RolePermNotAllow），`05_参考设计Top3` 需先在飞书 Base 手动新建同名空表；
> 建好后运行 `python3 scripts/sync_weekly_feishu.py --only 05` 灌数（应用可正常建字段/写记录）。未建表时同步自动跳过 05，不影响 01/02/03。

## 认证双模式

- **本地**：`gcloud auth application-default print-access-token`（ADC，已配置）
- **CI**：服务账号 JSON（`GOOGLE_APPLICATION_CREDENTIALS`）

属性 `502086217`，配额项目 `my-project-1579296929285`，必须带 `x-goog-user-project` header。

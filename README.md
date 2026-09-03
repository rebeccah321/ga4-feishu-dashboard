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

### 周报表与飞书拉取（`weekly-tables.yml`）
- 每周一 09:00 Asia/Shanghai 拉取 42 天数据并生成三张周度 CSV
- 导出 `dashboard/data/weekly/weekly_tables.json`（完整三表）和 `weekly_summary.json`（单条方案增长汇总）
- 提交到 GitHub Pages，供飞书多维表格自动化每周一 09:10 拉取

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
| `01_方案增长总览` | `周次` | `数据/analysis/方案增长总览.csv` |
| `02_单方案流量明细` | `最新周次 + solution名称` | `数据/analysis/单方案流量明细.csv` |
| `03_转化漏斗` | `最新周次 + 方案名称` | `数据/analysis/转换漏斗.csv` |

## 认证双模式

- **本地**：`gcloud auth application-default print-access-token`（ADC，已配置）
- **CI**：服务账号 JSON（`GOOGLE_APPLICATION_CREDENTIALS`）

属性 `502086217`，配额项目 `my-project-1579296929285`，必须带 `x-goog-user-project` header。

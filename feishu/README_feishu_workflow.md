# 飞书周度同步（不改变现有表结构）

飞书 Base 中已有四张表：

- `01_方案增长总览`
- `02_单方案流量明细`
- `03_转化漏斗`
- `04_社媒内容表现`

同步方案只读写前三张已存在的表，不创建、不重命名、不改表结构。

## 推荐：GitHub 主动同步三张表（多行明细更稳）

脚本：`scripts/sync_weekly_feishu.py`

它会：

- 拉取 `data/analysis/` 下三张 CSV
- 自动按表名找到飞书里已有的 `01/02/03` 表
- 按飞书现有列名写入
- 按已有记录 upsert：
  - `01`：`周次`
  - `02`：`最新周次 + solution名称`
  - `03`：`最新周次 + 方案名称`

本地 dry-run：

```bash
python3 scripts/sync_weekly_feishu.py --dry-run
```

真正同步：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_BITABLE_APP_TOKEN="bascn_xxx"
python3 scripts/sync_weekly_feishu.py
```

GitHub 自动化：`.github/workflows/weekly-feishu-sync.yml`
- `Weekly GA4 Tables` 成功后自动触发
- 或每周一 09:30 兜底触发
- 需要的 Secrets：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BITABLE_APP_TOKEN`

## 备选：飞书自动化每周拉取 01 表

`feishu/workflow_ga4_summary.json` 只写入已有 `01_方案增长总览` 表：

- 定时：每周一 09:10
- HTTP GET：`https://rebeccah321.github.io/ga4-feishu-dashboard/data/weekly/weekly_summary.json`
- 写入表：`01_方案增长总览`（不建表）

创建流程：

```bash
bash feishu/setup_feishu_workflow.sh <你的base_token>
```

注意：飞书原生自动化适合写单条总览记录；`02_单方案流量明细` 和 `03_转化漏斗` 每次是 12 行，建议使用上面的 GitHub 推送脚本。

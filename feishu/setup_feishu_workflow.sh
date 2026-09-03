#!/usr/bin/env bash
set -euo pipefail

# 用法：在飞书 Base 里创建自动化，写入已有的「01_方案增长总览」表。
#   bash feishu/setup_feishu_workflow.sh <base_token>
# 例：
#   bash feishu/setup_feishu_workflow.sh bascnxxxxxxxx

BASE_TOKEN="${1:?用法: $0 <base_token>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 创建 workflow（写入现有 01_方案增长总览表，不创建/不重命名表）==="
lark-cli base +workflow-create \
  --base-token "$BASE_TOKEN" \
  --workflow-json "@${SCRIPT_DIR}/workflow_ga4_summary.json" \
  --format json 2>&1

echo ""
echo "=== 完成 ==="
echo "Workflow 已创建。每周一 09:10 自动拉取 GitHub Pages weekly_summary.json，"
echo "并只写入已有的「01_方案增长总览」表。"
echo ""
echo "⚠️ 前提条件："
echo "  1. 飞书 Base 里已存在表名：01_方案增长总览"
echo "  2. weekly-tables workflow 已成功运行并提交 weekly_summary.json"
echo "  3. GitHub Pages 可访问："
echo "     https://rebeccah321.github.io/ga4-feishu-dashboard/data/weekly/weekly_summary.json"
echo ""
echo "多行明细表（02_单方案流量明细、03_转化漏斗）请使用 GitHub 推送方案："
echo "  python3 scripts/sync_weekly_feishu.py"

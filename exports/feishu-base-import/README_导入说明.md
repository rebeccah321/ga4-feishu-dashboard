# 飞书多维表格导入包

最快导入方式：

1. 打开飞书多维表格，新建一个空 Base。
2. 选择「导入 Excel/CSV」。
3. 优先上传 `seeed_解决方案增长_多维表格导入.xlsx`，它已经包含多张工作表。
4. 如果飞书没有自动识别多 Sheet，就逐个导入 `01_方案增长总览表.csv` 到 `05_社媒平台表现.csv`。

本包数据来源：

- GA4 网站真实数据：`ga4_normalized_with_solutions_2026-06-30_to_2026-07-27.csv`
- Seeed 解决方案页面：中文入口 `https://www.seeedstudio.com.cn/category/solutions-zh-hans`，英文入口 `https://www.seeed.cc/category/solutions`，以及 Solutions tab 下 12 个 solution 页面。
- 社媒平台原生数据：LinkedIn / X / FB / 小红书 / 抖音字段保留，当前等待平台后台或API补录。

包含表：

- 01_方案增长总览表: 1 rows
- 02_转化漏斗表: 12 rows
- 03_流量来源渠道: 5 rows
- 04_解决方案页面与行为: 5 rows
- 05_社媒平台表现: 6 rows

注意：

- `CTA总点击量`、`一键加购`、`销售跟进数` 目前先保留结构，数值暂为 0，等 CTA 事件或 CRM 数据接入后自动替换。
- `社媒总曝光量` 目前没有接入平台后台数据，暂填 0。
- 如果当前周数据未满 7 天，导出会自动使用最近完整周，避免周报被不完整数据拉低。
- `增长最快solution` 按最新周对比上周的 solution 页面访问量自动计算。
- 如果 12 个 solution 页面显示 `GA4未命中`，优先确认 seeed.cc / 中文站是否接入当前 GA4 Property `258704823`，或在下一次拉数中使用 `/solutions` 过滤专项拉取。

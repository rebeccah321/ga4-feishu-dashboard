# 飞书多维表格导入包

最快导入方式：

1. 打开飞书多维表格，新建一个空 Base。
2. 选择「导入 Excel/CSV」。
3. 优先上传 `seeed_解决方案增长_多维表格导入.xlsx`，它已经包含多张工作表。
4. 如果飞书没有自动识别多 Sheet，就逐个导入 `01_落地页官网增长看板.csv` 到 `08_字段说明.csv`。

本包数据来源：

- GA4 网站真实数据：`ga4_normalized_2026-06-30_to_2026-07-27.csv`
- Seeed 解决方案页面：中文入口 `https://www.seeedstudio.com.cn/category/solutions-zh-hans`，英文入口 `https://www.seeed.cc/category/solutions`，以及 Solutions tab 下 12 个 solution 页面。
- 社媒平台原生数据：LinkedIn / X / FB / 小红书 / 抖音字段保留，当前等待平台后台或API补录。

包含表：

- 01_落地页官网增长看板: 5 rows
- 02_单方案流量明细: 12 rows
- 03_流量来源渠道: 5 rows
- 04_热门页面与行为: 30 rows
- 05_看板指标总览: 10 rows
- 06_社媒平台表现: 6 rows
- 07_社媒内容表现: 5 rows
- 08_字段说明: 10 rows

注意：

- `新用户占比`、`加购率`、具体 CTA 点击事件目前标记为「待接入」，因为当前 GA4 导出没有 newUsers、add_to_cart、eventName 维度。
- `平均会话时长`当前用 GA4 的 `Avg Engagement Seconds` 近似。
- 如果 12 个 solution 页面显示 `GA4未命中`，优先确认 seeed.cc / 中文站是否接入当前 GA4 Property `258704823`，或在下一次拉数中使用 `/solutions` 过滤专项拉取。
- 后续发布社媒内容时，请统一使用表内建议的 UTM 参数，这样下一版可以把 LinkedIn/X/FB/小红书/抖音的网站效果拆开。

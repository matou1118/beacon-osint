# crawl-quality

## 触发

新增来源、调整 CSS Selector、启用 Crawl4AI、RSS/API 失效或网页抽取质量下降时使用。

## 目标

用最小、合规的样本验证采集链路，不追求绕过能力。

## 工作流

1. 阅读 `AGENTS.md`、`config/sources.yaml` 和采集器配置。
2. 确认来源状态为 `pending_review` 或已批准的 `enabled`。
3. 先执行静态/API/RSS，再按配置执行 Crawl4AI 或受控 Playwright 兜底。
4. 检查 robots、状态码、重定向、Content-Type、超时、链接、发布日期和正文长度。
5. 记录 `content_source`、`content_quality`、`needs_review`、失败原因和回退路径。
6. 给出 CSS Schema 是否需要调整的建议。

## 质量门槛

- 来源域名和最终 URL 在白名单内。
- 请求间隔至少 3 秒，遵守服务端限速。
- 列表页至少有可识别标题和详情链接。
- 详情页有标题和可用正文。
- 质量不足时回退或标记复核，不以空正文入库。

## 红线

不使用 stealth、代理池、指纹伪装、验证码绕过、登录绕过或高频重试。

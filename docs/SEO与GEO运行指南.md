# SEO 与 GEO：运行和上线准备

这份指南说明如何查看公开页面、配置正式域名，并检查上线后的抓取情况。没有正式域名时，本地页面默认不索引，不生成虚构的公网规范地址。

当前设计见[项目方案设计](./方案设计.md)，下文保留日常运行和维护步骤。

## 1. 本地查看与检查

前端依赖已安装时，在项目根目录打开 PowerShell，执行下面命令即可查看介绍和指南。公开说明不依赖后端；真实解析、登录和 AI 仍按 README 启动原有后端，且可能消耗原有服务额度。

```powershell
cd frontend
npm.cmd run dev
```

需要检查实际生产 HTML 时，使用构建与预览。预览不是正式部署；停止服务按 Ctrl+C。

```powershell
cd frontend
npm.cmd run build
npm.cmd run preview -- --port 5185 --strictPort
```

| 步骤 | 你要做什么 | 应该看到什么 |
| --- | --- | --- |
| 1 | 打开开发入口 `http://127.0.0.1:5173/` | 首页标题包含中文产品名称，原有解析输入框和功能入口可用 |
| 2 | 从首页常见问题或页脚进入使用指南 | 打开 `/guide/`，看到步骤、免费/VIP 额度、长视频分析范围与来源 |
| 3 | 在指南展开任意常见问题，缩窄到手机宽度 | 答案可展开，正文不超出屏幕；额度表可在自身区域滚动 |
| 4 | 构建后打开 `http://127.0.0.1:5185/` 并查看网页源代码 | 能看到公开功能和问答原文，不只含空应用容器 |
| 5 | 查看 `/guide/` 源代码或禁用 JavaScript 后刷新 | 仍可看到完整指南和样式；原生问答仍可展开 |
| 6 | 打开 `/robots.txt` 与 `/llms.txt` | 无域名时 robots 禁止抓取，资料索引用相对链接；`/sitemap.xml` 返回 404 |
| 7 | 查看 `/history`、`/queue`、`/billing` 的源代码 | 初始 HTML 有相应标题与 `noindex`，没有个人信息或公开结构化数据 |
| 8 | 打开一个不存在的路径 | HTTP 返回 404，页面提供返回首页链接，不能变成首页 200 |

上述步骤用于检查页面可读性和上线准备。需要体验真实下载时，使用自己有权访问的视频，按 README 启动后端并操作。

## 2. 获得域名后的配置

正式域名需要是 HTTPS 根网址，不带端口、路径、查询参数或登录信息。只有配置后重新构建，公开页才会允许索引并生成 canonical、完整分享地址与 sitemap；个人页面继续 noindex。

在 `frontend` 下将 `.env.example` 复制为本机 `.env.production`（已有文件不要覆盖），填写 `VITE_SITE_URL`，再运行生产构建。不要把真实配置或密钥提交到版本库；该配置只填写公开域名。

```powershell
cd frontend
if (!(Test-Path .env.production)) { Copy-Item .env.example .env.production }
notepad .env.production
npm.cmd run build
```

Docker 构建不复制本机环境文件。Compose 通过构建参数接收同一个配置；在项目根目录的 PowerShell 设置 `VITE_SITE_URL` 环境变量后，执行原有 `docker compose build frontend`。域名值会在构建时写入前端，修改后必须重建；仅改变预览服务器的环境变量不会改变既有产物。

| 页面 | 标题方向 | 抓取策略 | 维护位置 |
| --- | --- | --- | --- |
| 首页 `/` | 万能视频下载总结器 - SaveAny \| 视频下载与 AI 学习助手 | 正式域名构建后 index | `frontend/src/seo.js` |
| 指南 `/guide/` | 视频下载与 AI 总结使用指南 - SaveAny \| 字幕导出与常见问题 | 正式域名构建后 index | `frontend/src/seo.js`、`components/GuidePage.vue` |
| 工作台 `/workbench` | 视频工作台、视频下载与 AI 总结 | 始终 noindex，不公开用户视频信息 | `frontend/src/seo.js` |
| 历史 `/history` 及详情 | 我的历史、下载与总结记录 | 始终 noindex | 同上 |
| 队列 `/queue` | 批量任务、后台下载与字幕任务 | 始终 noindex | 同上 |
| 会员 `/billing` | 会员与订单、套餐权益与订单状态 | 始终 noindex，订单参数不写入公开地址 | 同上 |

维护负责人为项目维护者；本次更新日期为 2026-11-02。问答和额度变化时，同时更新共享内容、指南对比表和真实编辑日期，不要每次构建自动刷新日期。分享图是正式产品资源 `frontend/public/images/saveany-share.png`，1200×630、21,605 字节，不属于临时测试图片。

## 3. 上线后才能完成的事项

没有公网网址时无法证明搜索引擎已经收录，更不能证明 ChatGPT、Gemini 或其他 AI 已经引用。以下事项在实际部署后按真实数据检查。

| 事项 | 验收方法 |
| --- | --- |
| 公网与 HTTPS | 从外网访问首页、指南、CSS、分享图、robots 和 sitemap，确认 HTTPS 证书有效，CDN/WAF 没有挡住正常抓取 |
| 收录准备 | 使用 Google Search Console、百度搜索资源平台及必应站长工具验证站点，按平台要求提交网站地图 |
| 抓取效果 | 用平台 URL 检查查看实际抓取 HTML、canonical 和响应码，确认私人页不被收录 |
| 分享与结构化数据 | 检查真实分享卡片与 Schema 语法；结构化数据不保证获得富媒体展示 |
| 性能 | 正式部署后使用 PageSpeed Insights 与实际用户数据观察 Core Web Vitals，不能用本地构建成功替代 |
| AI 引用 | 固定日期、平台、问题和是否开启联网搜索，记录品牌提及、引用 URL 及准确性；与上线前基线比较 |

可用的问题例子：“有没有能下载视频并基于字幕生成中文总结的在线工具？”“SaveAny 长视频总结能覆盖多长内容？”“视频没有字幕时如何总结？”不要把让 AI 直接打开已知网址的成功，等同于自然搜索问题中的主动推荐。

优化依据包括 [Google JavaScript SEO 指南](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics)和 [Google AI 搜索功能说明](https://developers.google.com/search/docs/appearance/ai-features)。其 AI 搜索沿用 SEO 基础要求，无需特殊 AI 标记，满足要求也不保证收录或展示。`robots.txt` 是抓取提示，个人信息仍由现有后端鉴权保护；允许训练爬虫与允许搜索抓取并非同一件事，不能把放行训练当成 AI 推荐的保证。

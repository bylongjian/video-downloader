# 万能视频下载网站 - SEO 与 GEO 运行指南

## 1. 页面范围

首页和公开指南提供产品介绍，个人功能页保留通用页面信息并禁止索引。页面设计见[方案设计](./方案设计.md)，业务功能见[需求分析](./需求分析.md)。

| 页面 | 内容 | 索引规则 |
|------|------|----------|
| `/` | 功能、步骤与常见问题 | 配置正式域名并构建后允许索引 |
| `/guide/` | 使用说明、额度、限制和来源 | 同首页 |
| `/workbench` | 视频工作台 | 始终 noindex |
| `/history`、`/queue`、`/billing` | 个人历史、任务与订单 | 始终 noindex，不进入网站地图 |
| 未知路径 | 返回首页入口 | HTTP 404，不索引 |

没有正式域名时，公开页也保持 noindex，不生成 canonical、绝对分享地址或 sitemap。抓取规则不替代后端的个人数据认证。

## 2. 本地查看

开发入口用于查看页面和交互；检查构建后的 HTML 时，另开终端运行生产预览。完整下载与 AI 功能仍按[本地运行指南](./保姆级本地运行指南.md)启动后端。

**开发页面：**

```powershell
cd frontend
npm.cmd run dev
```

打开 http://127.0.0.1:5173/ 和 `/guide/`。

**生产页面预览：**

```powershell
cd frontend
npm.cmd run build
npm.cmd run preview -- --port 5185 --strictPort
```

打开 http://127.0.0.1:5185/ ，查看网页源代码应能读到首页和指南正文。预览用于检查构建产物，停止时按 `Ctrl+C`。

## 3. 正式域名配置

站点地址通过 `VITE_SITE_URL` 在构建时写入。使用真实 HTTPS 根网址，不带端口、路径、查询参数或登录信息，修改后重新构建。

### 3.1 普通构建

在前端配置文件填写公开域名，已有文件保留。此处只配置公开站点地址，不填写后端密钥。

```powershell
cd frontend
if (!(Test-Path .env.production)) { Copy-Item .env.example .env.production }
notepad .env.production
npm.cmd run build
```

### 3.2 Docker 构建

在项目根目录终端设置 `VITE_SITE_URL` 为正式站点地址，再构建并更新前端容器。Compose 通过构建参数传入该值，不复制本机前端环境文件。

```powershell
docker compose build frontend
docker compose up -d frontend
```

仅修改预览进程的环境变量不会改变已有产物。Docker 基础配置见[扩展运行指南](./【剩余扩展】本地运行与验收.md)。

## 4. 内容维护

首页与指南共用公开内容，额度或操作规则变化时在同一来源更新，避免页面和结构化问答不一致。

| 文件 | 维护内容 |
|------|----------|
| frontend/src/content/product.js | 产品说明、常见问题、来源与实际编辑日期 |
| frontend/src/seo.js | 标题、描述、分享信息、JSON-LD 和抓取规则 |
| frontend/src/pageMetadata.js | 浏览器导航时更新页面信息 |
| frontend/src/components/GuidePage.vue | 指南结构与展示 |
| frontend/seo-plugin.js | 预渲染、robots、sitemap、llms.txt 和静态路由 |
| frontend/public/images/saveany-share.png | 1200×630 产品分享图 |

公开资料不包含个人视频链接、字幕、总结或订单，不编造评价和排名。`llms.txt` 作为资料入口，不保证所有 AI 服务都会读取。

## 5. 上线检查

本地可检查页面内容与配置，真实搜索收录、性能和 AI 引用需要公开上线后观察。

- **页面响应**：公开页正常返回，未知路径为 404，个人页初始响应包含 noindex
- **域名一致**：canonical、分享地址、robots 和 sitemap 使用同一正式域名，只列公开页
- **正文可读**：禁用 JavaScript 后仍能阅读首页和指南，问答与 JSON-LD 内容一致
- **公网访问**：HTTPS、CSS、图片与抓取文件可从外网读取，代理或防护不误拦正常访问
- **收录与引用**：按站长平台要求验证站点和提交地图，记录实际抓取、性能与 AI 引用情况

维护资料可参考 [Google JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics) 与 [Google AI 搜索说明](https://developers.google.com/search/docs/appearance/ai-features)。配置完整不代表已经收录或获得推荐。

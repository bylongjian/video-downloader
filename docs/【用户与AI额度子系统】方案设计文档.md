# 万能视频下载网站 - 用户与 AI 额度子系统方案设计

## 1. 技术架构

用户系统负责登录状态、个人数据归属和 AI 使用额度。视频解析与单条下载向游客开放，AI 和个人数据接口通过服务端检查身份与额度。

```
Vue3 前端
    ├─ AuthModal          → 邮箱注册 / 登录
    ├─ useAuth            → 身份恢复 / 退出 / 额度刷新
    └─ VideoSummary       → 恢复保存结果 / 总结 / 问答
                │ HTTP API / SSE
FastAPI 后端
    ├─ auth.py            → JWT 验证与会话检查
    ├─ quota.py           → 总结、问答额度预占与结算
    ├─ extension_quota.py → 翻译、转写额度
    └─ database.py        → 账号、会话与成功结果
                │
              SQLite
```

功能需求见[需求分析](./需求分析.md)，视频处理和扩展模块的总体结构见[方案设计](./方案设计.md)。

## 2. 技术栈选型

账号和额度使用本地 SQLite 保存，无需安装独立数据库服务。认证采用密码哈希、JWT 和服务端会话，既能恢复登录，也能让退出后的 Token 失效。

| 层次 | 技术 | 选型理由 |
|------|------|----------|
| 用户接口 | FastAPI + Pydantic | 处理注册、登录、身份与输入校验 |
| 密码保存 | bcrypt | 保存密码哈希，不保存明文密码 |
| 登录认证 | PyJWT + 服务端会话 | Token 验证身份，会话记录支持退出撤销 |
| 数据存储 | SQLite + sqlite3 | 本地持久化，支持事务和并发额度检查 |
| 前端状态 | Vue 组合函数 useAuth | 共享身份和额度，统一处理账号变化 |
| 时间计算 | zoneinfo + tzdata | 固定使用北京时间计算每日额度 |

## 3. API 设计

注册和登录返回 Token，后续个人接口通过 `Authorization: Bearer <token>` 传递身份。JSON 接口统一使用 `success / data` 返回成功结果，失败时返回 `code / error`。

### 3.1 注册与登录

邮箱去除首尾空白并转为小写，密码要求 8～50 个字符，UTF-8 编码不超过 72 字节。注册成功后直接登录，返回用户信息与额度。

```
POST /api/auth/register
Content-Type: application/json

Request:
{
    "email": "user@example.com",
    "password": "example-password"
}

Response (201):
  success: true
  data: {token, token_type: "bearer", user: {id, email}, quota}

POST /api/auth/login
Request: 同注册
Response (200): 同注册成功结果
```

### 3.2 身份、退出与额度查询

页面刷新时查询当前用户，生成完成后刷新额度。退出只撤销当前登录会话，其他独立登录会话继续有效。

```
Authorization: Bearer <token>

GET  /api/auth/me      → 返回 {user, quota}
GET  /api/auth/quota   → 返回四项额度及会员状态
POST /api/auth/logout → 返回 {logged_out: true}
```

额度查询示例：

```json
{
    "success": true,
    "data": {
        "date": "2026-11-02",
        "timezone": "Asia/Shanghai",
        "server_time": "2026-11-02T12:00:00+08:00",
        "reset_at": "2026-11-03T00:00:00+08:00",
        "summary": {"limit": 3, "used": 1, "reserved": 0, "remaining": 2},
        "chat": {"limit": 10, "used": 0, "reserved": 0, "remaining": 10},
        "translation": {"limit": 3, "used": 0, "reserved": 0, "remaining": 3, "unit": "requests"},
        "transcription": {"limit": 3600, "used": 0, "reserved": 0, "remaining": 3600, "unit": "seconds"},
        "membership": {"tier": "free", "payment_mode": "test", "expires_at": null}
    }
}
```

### 3.3 总结恢复与 AI 请求

打开视频时先查询本人保存结果。只有没有结果或用户主动重新总结时，才发起新的生成请求；查询失败时提示重试，不自动当作无结果。

```
Authorization: Bearer <token>

GET  /api/summarize/result?url=<视频原始链接>&language=zh
                              → 返回 {subtitle, summary, mindmap, saved_at, result_id}
                              → 没有保存结果时 data 为 null
POST /api/summarize           → 生成总结和导图，成功使用总结额度
POST /api/chat                → 视频问答，成功使用问答额度
```

- **总结事件**：subtitle → summary → mindmap → quota → done
- **问答事件**：answer → quota → done
- **失败事件**：error，不发送 done；未成功的预占由请求结束时释放
- **数据格式**：SSE 数据使用 JSON 编码，done 使用 `[DONE]`；前端按事件类型解码

### 3.4 错误处理

身份和额度检查在 SSE 开始前完成，模型执行中的错误通过 error 事件返回。前端根据错误码提示登录、等待额度或重试。

| 状态 | 错误码 | 页面处理 |
|------|--------|----------|
| 401 | AUTH_REQUIRED / SESSION_INVALID | 引导登录或清理失效身份 |
| 401 | INVALID_CREDENTIALS | 提示邮箱或密码错误 |
| 409 | EMAIL_EXISTS | 提示切换到登录 |
| 422 | VALIDATION_ERROR | 显示输入错误 |
| 429 | QUOTA_EXHAUSTED / QUOTA_BUSY | 显示剩余额度、处理中占用和恢复时间 |
| 503 | ACCOUNT_SERVICE_UNAVAILABLE | 提示账号服务暂不可用，保留当前视频 |

## 4. 登录与会话策略

登录状态由 Token 和服务端会话共同决定。浏览器只保存 Token，不保存密码、权限结果或成功总结正文。

1. **注册/登录**：校验邮箱和密码 → 保存或核对密码哈希 → 创建会话 → 签发 JWT
2. **访问个人接口**：校验 JWT 的 HS256 签名、sub / jti / iat / exp → 检查会话归属、到期和撤销状态
3. **恢复身份**：刷新后调用 `/api/auth/me`，恢复用户与服务端额度
4. **退出/换号**：撤销当前会话或更换 Token，取消旧请求并清空旧账号内容；当前视频仍可下载

- Token 和会话有效期均为 **72 小时**，不自动续期
- `JWT_SECRET` 至少 32 字节，缺少有效配置时停止后端启动
- 同源标签页同步 Token 变化，其他独立会话不受影响
- 个人历史、任务、字幕和订单均按当前用户查询，不接受客户端指定其他用户身份

## 5. AI 额度策略

四项能力分别计量，免费与 VIP 使用同一套成功结算规则。预占用于防止并发超用，完整成功后才记入已用额度。

| 能力 | 免费账号 | VIP | 计量单位 |
|------|----------|-----|----------|
| 总结 | 3 次/日 | 30 次/日 | 摘要、导图和保存完整成功计 1 次 |
| 问答 | 10 次/日 | 100 次/日 | 每轮完整回答计 1 次 |
| 翻译 | 3 次/日 | 20 次/日 | 完整译文成功计 1 次 |
| 转写 | 60 分钟/日 | 240 分钟/日 | 实际音频秒数向上取整 |

### 5.1 预占与结算

额度检查和预占在同一数据库短事务内完成，网络请求与模型生成不占用数据库事务。

```
登录有效 → 检查额度 → 预占
                       ├─ 完整成功 → 保存结果并结算 → quota / done
                       └─ 失败 / 取消 / 过期 → 释放预占
```

- **可用额度**：`remaining = max(0, limit - used - reserved)`
- **预占期限**：总结、问答和翻译为 15 分钟；转写为 30 分钟
- **换日规则**：服务端北京时间零点恢复，跨日任务计入预占日期
- **会员变化**：到期恢复免费上限，当天已用量不清零；已合法预占任务可按原授权完成
- **断流处理**：服务端已经成功结算时不回滚，前端重新查询实际额度和保存结果

### 5.2 成功结果恢复

同账号、同原始链接、同总结语言只保留最新成功结果。再次打开直接读取，不重新提取字幕、不调用模型，也不使用额度。

- 主动重新总结成功后更新结果并计 1 次，失败保留上次成功内容
- 字幕、摘要、导图和成功计次在同一事务保存，保存失败时一起回滚
- 问答正文只保留在当前页面，不持久保存
- 总结历史和删除规则见[扩展接口与数据方案](./【剩余扩展】接口与数据方案.md)

## 6. 数据存储设计

账号基础数据和成功总结保存在 SQLite，扩展功能通过版本迁移增加表和索引。数据库默认位于 `backend/data/app.db`，升级前自动备份，不删除旧库。

| 数据表 | 主要内容 | 保存规则 |
|--------|----------|----------|
| users | 邮箱、密码哈希、创建与更新时间 | 邮箱唯一，不返回密码哈希 |
| auth_sessions | 会话 ID、用户、到期与撤销时间 | 支持当前会话退出和独立会话隔离 |
| ai_usage_requests | 总结/问答、预占日期、状态和期限 | 状态为 reserved / succeeded / released |
| summary_results | 链接、语言、字幕、摘要、导图和稳定 result_id | 按用户、链接、语言唯一保存 |

`summary_results` 同时保存标题和平台供历史列表展示。翻译/转写额度使用 `extension_usage_requests` 独立计量，相关结果、历史和订单结构见扩展方案。

## 7. 前端页面设计

登录入口和额度提示沿用现有页面风格。账号变化时统一清理旧状态，避免旧响应写入新账号页面。

- **登录弹窗**：邮箱注册/登录切换、密码显隐、输入错误、提交状态、Esc 关闭和焦点返回
- **页头入口**：游客显示登录，已登录显示邮箱、退出及个人功能入口
- **AI 面板**：等待身份恢复，游客显示引导；登录后先恢复保存结果，再决定是否首次生成
- **额度展示**：显示已用、预占、剩余和恢复时间，生成完成、窗口重新激活及换日时更新
- **账号切换**：共享 `sessionVersion`，请求回调核对账号与视频，退出后清空 AI 内容并保留视频信息

## 8. 项目目录结构

后端按认证、数据和额度划分模块，前端使用统一身份状态。视频下载和模型模块继续负责各自的处理逻辑。

```
backend/
├── auth.py               # 密码哈希、JWT 与会话检查
├── api_auth.py           # 注册、登录、退出与额度接口
├── database.py           # 账号、会话和成功总结
├── quota.py              # 总结/问答预占与结算
├── extension_quota.py    # 翻译/转写额度
└── api_summarize.py      # AI 接口与成功结果恢复
frontend/src/
├── api/auth.js           # 账号 API 与 Token
├── api/summarize.js      # AI 请求与保存结果查询
├── composables/useAuth.js # 共享身份与额度
└── components/AuthModal.vue # 注册/登录弹窗
```

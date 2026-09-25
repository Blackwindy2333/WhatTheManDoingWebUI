# 在干什么 · WebUI

部署在服务器上的监控展示页：普通访客只读查看各设备「在干什么」（前台应用）；管理员通过配置文件中的 Token 登录后管理设备、WebUI 配置、访问统计与连接方式。

数据来自各台 [WhatTheManDoing](../WhatTheManDoing) 的 JSON API（见主项目 `docs/API.md`）。浏览器**只访问本 WebUI**，设备地址与 Token 由服务端代理持有。

## 功能

**访客（只读）**

- 多设备卡片：设备名、在线/离线/已暂停、当前应用、健康角标（延迟 / HTTP / 更新时间）
- 按后台配置的刷新间隔自动更新（SSE 实时推送，失败回退轮询）
- 可选「最近切换」时间线
- 不可进行任何修改

**管理后台**

- Token 登录 → 短期会话；连续登录失败超阈值封禁 IP 24 小时
- 添加 / 编辑 / 删除监控设备（显示名、API 请求地址、Viewer Token、启停）
- 设备「测试连接」、导出设备列表 JSON
- WebUI 配置：标题、刷新间隔、每页设备数、是否显示历史、信任反向代理等
- 连接方式：
  - 设备侧：默认协议 `http` / `https`，每台设备 URL 可写完整前缀
  - WebUI 自身：`serve.mode` = `http` | `https`（https 需证书路径）
- 访问计数：总量 + 按日；登录失败也计入请求访问
- 封禁列表 / 解封、审计日志

## 快速开始

```bash
# 依赖
pip install -r requirements.txt

# 首次运行会生成 config.json（默认值）与 state.json
python -m server
# 默认 http://127.0.0.1:8080
```

配置模板见 [`config.example.json`](config.example.json)。**真实 `config.json` / `state.json` 不会提交到 git。**

修改管理员 Token、设备列表或监听方式：

1. 直接改 `config.json` 后重启进程；或  
2. 登录管理后台（默认 Token 为 `change-me`，请立刻修改）在线修改。

## 测试

开发过程中**不要**用启动应用的方式联调，仅跑 pytest：

```bash
python -m pytest
```

## 目录

| 路径 | 说明 |
|------|------|
| `index.html` / `styles.css` / `app.js` | 唯一前端入口（公开页 + 管理面板） |
| `server/` | FastAPI：聚合代理、鉴权/封禁、访问统计、管理 API |
| `config.example.json` | 配置模板 |
| `DESIGN.md` | UI 设计令牌与降级策略 |
| `tests/` | pytest（TestClient，不启动真实 uvicorn） |

## 配置摘要

| 字段 | 默认 | 说明 |
|------|------|------|
| `admin_token` | `change-me` | 管理员登录 Token |
| `login_max_failures` | `5` | 连续失败次数，达到即封禁 |
| `ban_duration_hours` | `24` | 封禁时长 |
| `login_rate_limit_per_minute` | `10` | 登录接口限流 |
| `session_ttl_seconds` | `3600` | 管理会话有效期 |
| `refresh_interval_seconds` | `5` | 聚合刷新 / 前端刷新间隔（1–60） |
| `show_history` | `true` | 是否展示历史时间线 |
| `default_device_scheme` | `http` | 设备 URL 无协议时的默认前缀 |
| `trust_proxy` | `false` | 是否信任 `X-Forwarded-For` |
| `forwarded_allow_ips` | `*` | uvicorn 可信代理 IP（穿透/反代） |
| `serve.mode` | `http` | WebUI 自身对外协议 |
| `log.level` | `INFO` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `log.dir` | `logs` | 日志目录（**git 忽略**） |
| `log.filename` | `webui.log` | 主日志文件名，按大小轮转 |
| `log.max_bytes` | `5242880` | 单文件上限（约 5MB） |
| `log.backup_count` | `5` | 轮转保留份数 |
| `log.console` | `true` | 是否同时打到控制台 |
| `log.access_log` | `true` | 是否记录 HTTP 访问日志 |
| `devices[]` | 见 example | `id` / `name` / `api_base_url` / `viewer_token` / `enabled` |

## API（本 WebUI）

公开：`GET /api/public/config` · `GET /api/public/devices` · `GET /api/public/devices/{id}/history` · `GET /api/public/stream`（SSE）

管理（`Authorization: Bearer <session>`）：`POST /api/admin/login` · `logout` · `me` · `config`（GET/PATCH）· `devices` CRUD · `devices/{id}/test` · `devices/export` · `stats` · `bans` · `audit`

响应 envelope 与主项目一致：`{code, message, data}`。

---

## 内网穿透 / 公网访问

终端出现下列警告时，**几乎都是「HTTPS 或非法字节」直接打进了 HTTP 端口**（浏览器/扫描器/TLS ClientHello），不是业务逻辑崩溃：

```text
WARNING:  Invalid HTTP request received.
h11._util.LocalProtocolError: can't handle event type Response when role=SERVER and state=MUST_CLOSE
```

### 正确接入方式（二选一）

| 方式 | 穿透/反代侧 | 本 WebUI `serve` | 浏览器访问 |
|------|-------------|------------------|------------|
| **A. 隧道终结 TLS（推荐）** | 提供 `https://`，解密后转 **HTTP** 到 `127.0.0.1:8080` | `mode: "http"` | `https://你的域名` |
| **B. TLS 透传 / 本机 HTTPS** | 原样转发 TCP，或本机直接挂证书 | `mode: "https"` + 证书路径 | `https://你的域名或IP:端口` |

若隧道只给了 `http://`，请用 **http://** 打开；不要把 `https://` 指到未开 TLS 的端口。

### 排查步骤

1. 本机先确认：`http://127.0.0.1:8080/` 正常。  
2. 打开 `GET /api/public/probe`：看 `scheme`、`x_forwarded_proto`、`resolved_ip`。  
3. 公网若走反代/隧道：把 `trust_proxy` 设为 `true`（可选 `forwarded_allow_ips` 限制可信来源，如隧道节点 IP；默认 `*`）。  
4. 仍见 `Invalid HTTP`：多半是扫描器或浏览器用了错误协议——属噪音；确认业务页面能打开即可。  
5. 自签证书告警属浏览器行为，与本错误无关。

### 相关配置

| 字段 | 说明 |
|------|------|
| `serve.mode` | `http` / `https`（https 必须填证书） |
| `trust_proxy` | 信任 `X-Forwarded-For` / 真实 IP，用于封禁与计数 |
| `forwarded_allow_ips` | 交给 uvicorn 的可信代理 IP（`*` 或逗号分隔） |

---

## 日志

默认写入 `logs/webui.log`（按 `max_bytes` / `backup_count` 轮转为 `webui.log.1` …），可选同步控制台。**`logs/` 与 `*.log` 已在 `.gitignore` 中，不会被 git 跟踪。**

| 通道 | logger | 内容 |
|------|--------|------|
| 访问 | `webui.http` | 方法、路径、状态码、耗时、客户端 IP；封禁拒绝；未捕获异常堆栈 |
| 鉴权 | `webui.auth` | 登录成功/失败、限流、封禁、登出 |
| 聚合 | `webui.aggregate` | 各设备拉取成功（DEBUG）/失败、刷新批次异常 |
| 设备 | `webui.devices` | 测试连接结果 |
| 启停 | `webui.startup` | 进程启动/停止、监听参数 |

管理后台「审计」页可点「查看最近日志」；或调用 `GET /api/admin/logs?lines=100`。  
修改 `log.*` 配置会立即重建日志 handler（级别/路径生效）；`serve.*` 仍需重启进程。

---

## 注意点与风险（实现/运维必读）

1. **多设备轮询可能打爆主项目全局 RPM**  
   主项目 `rate_limit_per_minute` 为全局额度。本 WebUI 在服务端缓存聚合结果，前端只打 WebUI；请保持 `refresh_interval_seconds` ≥ 3，并关注上游 `X-RateLimit-*`。设备很多时适当加大间隔。

2. **反向代理后真实客户端 IP**  
   默认取 `request.client.host`。若前面有 Nginx/Caddy 等，请将 `trust_proxy` 设为 `true`，以便封禁与计数按 `X-Forwarded-For` 第一段 IP 生效。错误地信任代理头可能被伪造 IP，仅在可信代理后开启。

3. **HTTPS / 自签证书**  
   `serve.mode=https` 需要可读的 `ssl_certfile` 与 `ssl_keyfile`；自签证书浏览器会告警。生产环境更常见的是反向代理终止 TLS，WebUI 保持 `http` 并只监听本机。

4. **配置被 gitignore 后换机易丢**  
   真实 `config.json`、`state.json` 不入库（含 Token）。迁移服务器时请自行备份；仓库仅提供 `config.example.json`。

5. **封禁 / 会话存储位置**  
   封禁与访问计数持久化在 `state.json`；**会话仅存内存**，进程重启后需重新登录（短期会话可接受）。

6. **登录封禁阈值**  
   默认连续 5 次失败封 24h（`login_max_failures` / `ban_duration_hours`）。与登录限流叠加；误封可在「安全」页解封，或编辑 `state.json` 后重启。

7. **设备 API 凭据**  
   `viewer_token` 仅保存在服务端配置，不会下发给访客页面。配置文件权限请收紧（NTFS/chmod），避免同机其他用户读取。

8. **上游兼容**  
   聚合优先请求 `GET /devices/{id}`，回退 `/devices`、`/status`。请保证各 WhatTheManDoing 版本支持 envelope 与只读查询；历史走 `/devices/{id}/history`，回退 `/status/history`。

9. **隐私**  
   本 WebUI 不放宽主项目隐私策略（窗口标题默认不下发、黑名单在 Agent 侧脱敏）。`show_history` 与标题展示以服务端返回字段为准。

10. **Apple UI 降级**  
    已实现半透明材质、按压反馈、排版字距、`prefers-reduced-motion` / `prefers-reduced-transparency`。未引入弹簧动画库与手势拖拽，避免复杂度。

11. **监听变更需重启**  
    后台修改 `serve.*` 只写入配置；**必须重启 `python -m server` 才会切换端口或 TLS**。刷新间隔等业务参数即时生效。

12. **不要在开发中期启动应用联调**  
    以 `python -m pytest` 为准；全部代码完成后再做运行时验证。

13. **日志体积与脱敏**  
    访问日志会记录 IP 与路径，不会记录 `admin_token` / `viewer_token`。若日志目录在共享盘，请自行收紧目录权限；`backup_count=0` 表示不保留旧轮转文件。

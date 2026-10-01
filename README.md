# 在干什么 · WebUI

部署在服务器上的**只读**监控展示页：访客查看各设备「在干什么」（前台应用）。  
**没有管理后台**——设备列表与 WebUI 配置**只能**通过 `config.json` 修改（改完重启进程）。

数据来自各台 [WhatTheManDoing](../WhatTheManDoing) 的 JSON API（见主项目 `docs/API.md` v1）。浏览器**只访问本 WebUI**，设备地址与 `api_token` 由服务端代理持有，不会下发给访客。

## 功能

- 多设备卡片：设备名、在线/离线/已暂停、当前应用、健康角标（延迟 / HTTP / 更新时间）
- 按配置的刷新间隔自动更新（SSE 实时推送，失败回退轮询）
- 可选「最近切换」时间线
- 纯只读：无登录、无写接口、无配置 API

## 快速开始

```bash
# 依赖
pip install -r requirements.txt

# 首次运行会生成 config.json（默认值）
python -m server
# 默认 http://127.0.0.1:8080
```

配置模板见 [`config.example.json`](config.example.json)。**真实 `config.json` 不会提交到 git。**

修改设备列表、Token、监听方式：**直接编辑 `config.json` 后重启** `python -m server`。

### 配置工具（推荐）

仓库根目录提供便捷脚本（底层都是 `config_cli.py`，写入前会走 `server.config` 校验）：

| 平台 | 入口 |
|------|------|
| Windows | `config.bat` |
| macOS / Linux / Git Bash | `config.sh`（需 `chmod +x config.sh`） |

```bat
config.bat                 :: 交互菜单
config.bat show            :: 查看完整配置
config.bat show serve.port --raw
config.bat set serve.port 9090
config.bat validate
config.bat edit            :: 记事本打开并校验
config.bat device list
config.bat device add --id lap-1 --name Laptop --url http://192.168.1.10:8765/api/v1 --token <api_token>
config.bat device set lap-1 --enabled false
config.bat device remove lap-1
config.bat backup
config.bat restore config.json.bak-20260101-120000
config.bat export --out backup.json
```

```bash
./config.sh                # 交互菜单
./config.sh set page_title "在干什么"
./config.sh device list
```

常用子命令：`show` / `path` / `validate` / `init` / `edit` / `keys` / `set` / `device` / `backup` / `restore` / `export`。  
`set` 支持的键见 `config_cli.py keys`；设备用 `device` 子命令管理。也可用 `python config_cli.py ...` 直接调用。

改完配置后仍需重启 `python -m server` 才会生效。

### 对接上游（WhatTheManDoing v1）

每台设备一条配置，指向该机器 API 根地址，并填写与上游 `server/config.json` 一致的 `api_token`：

```json
{
  "id": "my-pc",
  "name": "My PC",
  "api_base_url": "http://127.0.0.1:8765/api/v1",
  "api_token": "与上游 api_token 相同",
  "enabled": true
}
```

请求头为 `Authorization: Bearer <api_token>`。上游空 Token 时受保护接口会 401。  
取数路径：`GET {base}/devices/{id}` → 回退 `{base}/devices` → `{base}/status`；历史 `{base}/devices/{id}/history`（404 时 `/status/history`）。

> 旧字段 `viewer_token` / `admin_token` 等已移除；配置里若仍出现会直接拒绝启动。

## 测试

开发过程中**不要**用启动应用的方式联调，仅跑 pytest：

```bash
python -m pytest
```

## 目录

| 路径 | 说明 |
|------|------|
| `index.html` / `styles.css` / `app.js` | 唯一前端入口（公开只读页） |
| `server/` | FastAPI：聚合代理 + 公开 API |
| `config.example.json` | 配置模板 |
| `DESIGN.md` | UI 设计令牌与降级策略 |
| `tests/` | pytest（TestClient，不启动真实 uvicorn） |

## 配置摘要

| 字段 | 默认 | 说明 |
|------|------|------|
| `refresh_interval_seconds` | `5` | 聚合刷新 / 前端刷新间隔（1–60） |
| `page_title` / `page_subtitle` | 见 example | 页面标题 |
| `show_history` | `true` | 是否展示历史时间线 |
| `devices_per_page` | `12` | 预留分页密度 |
| `trust_proxy` | `false` | 是否信任 `X-Forwarded-For` |
| `forwarded_allow_ips` | `*` | uvicorn 可信代理 IP（穿透/反代） |
| `serve.mode` | `http` | WebUI 自身对外协议（https 需证书） |
| `log.*` | 见 example | 级别 / 目录 / 轮转 / 控制台 / 访问日志 |
| `devices[]` | 见 example | `id` / `name` / `api_base_url` / `api_token` / `enabled` |

**改配置 = 编辑文件 + 重启进程。** 没有在线修改入口。

## API（本 WebUI）

公开（无鉴权）：

- `GET /api/public/config`
- `GET /api/public/probe`
- `GET /api/public/devices`
- `GET /api/public/devices/{id}/history`
- `GET /api/public/stream`（SSE）

不存在 `/api/admin/*`。响应 envelope 与主项目一致：`{code, message, data}`。

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
| `trust_proxy` | 信任 `X-Forwarded-For` / 真实 IP（用于访问日志） |
| `forwarded_allow_ips` | 交给 uvicorn 的可信代理 IP（`*` 或逗号分隔） |

---

## 日志

默认写入 `logs/webui.log`（按 `max_bytes` / `backup_count` 轮转为 `webui.log.1` …），可选同步控制台。**`logs/` 与 `*.log` 已在 `.gitignore` 中，不会被 git 跟踪。**

| 通道 | logger | 内容 |
|------|--------|------|
| 访问 | `webui.http` | 方法、路径、状态码、耗时、客户端 IP；未捕获异常堆栈 |
| 聚合 | `webui.aggregate` | 各设备拉取成功（DEBUG）/失败、刷新批次异常 |
| 启停 | `webui.startup` | 进程启动/停止、监听参数 |

修改 `log.*` 需重启进程（无热更新入口）；`serve.*` 同样需重启。

---

## 注意点与风险（实现/运维必读）

1. **多设备轮询可能打爆主项目全局 RPM**  
   主项目 `rate_limit_per_minute` 为全局额度。本 WebUI 在服务端缓存聚合结果，前端只打 WebUI；请保持 `refresh_interval_seconds` ≥ 3，并关注上游 `X-RateLimit-*`。设备很多时适当加大间隔。

2. **反向代理后真实客户端 IP**  
   默认取 `request.client.host`。若前面有 Nginx/Caddy 等，请将 `trust_proxy` 设为 `true`，以便访问日志按 `X-Forwarded-For` 第一段 IP 记录。错误地信任代理头可能被伪造 IP，仅在可信代理后开启。

3. **HTTPS / 自签证书**  
   `serve.mode=https` 需要可读的 `ssl_certfile` 与 `ssl_keyfile`；自签证书浏览器会告警。生产环境更常见的是反向代理终止 TLS，WebUI 保持 `http` 并只监听本机。

4. **配置被 gitignore 后换机易丢**  
   真实 `config.json` 不入库（含 Token）。迁移服务器时请自行备份；仓库仅提供 `config.example.json`。

5. **无后台 = 无在线改配置**  
   一切变更走文件；请在变更窗口重启进程。配置校验失败会**拒绝启动**（含仍写着 `viewer_token` / `admin_token` 的旧文件）。

6. **设备 API 凭据**  
   `api_token` 仅保存在服务端配置，不会下发给访客页面。配置文件权限请收紧（NTFS/chmod），避免同机其他用户读取。

7. **上游兼容**  
   聚合优先请求 `GET /devices/{id}`，回退 `/devices`、`/status`。请保证上游为 v1 单 `api_token` 模型；历史走 `/devices/{id}/history`，回退 `/status/history`。

8. **隐私**  
   本 WebUI 不放宽主项目隐私策略（窗口标题默认不下发、黑名单在 Agent 侧脱敏）。`show_history` 与标题展示以服务端返回字段为准。

9. **Apple UI 降级**  
   已实现半透明材质、按压反馈、排版字距、`prefers-reduced-motion` / `prefers-reduced-transparency`。未引入弹簧动画库与手势拖拽，避免复杂度。

10. **监听变更需重启**  
    改 `serve.*` 后**必须重启 `python -m server`** 才会切换端口或 TLS。

11. **不要在开发中期启动应用联调**  
    以 `python -m pytest` 为准；全部代码完成后再做运行时验证。

12. **日志体积与脱敏**  
    访问日志会记录 IP 与路径，不会记录 `api_token`。若日志目录在共享盘，请自行收紧目录权限；`backup_count=0` 表示不保留旧轮转文件。

13. **公网只读面**  
    无登录即意味着**任何能打开页面的人都能看设备状态**。请自行在反代/隧道层加访问控制（IP 白名单、Basic Auth、Cloudflare Access 等），或仅监听内网。

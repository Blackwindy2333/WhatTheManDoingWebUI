---
feature: api-v1-config-only
status: delivered
updated: 2026-02-15
branch: main
commits: f776f5b..5a358e6
---

# API v1 接入 · 仅配置文件改配置

## Report

**What was built** — WebUI 对齐上游 WhatTheManDoing v1：每台设备使用 `api_token`，轮询/历史请求头为 `Authorization: Bearer <api_token>`（空 token 不发鉴权头）；HTTP 401/403 与 envelope `code=40100` 一律报 `unauthorized (api_token?)`，历史接口鉴权失败返回 401 而非伪装空列表。旧键 `viewer_token` 与全部后台配置项在校验层直接拒绝。

网页管理后台整体拆除：无登录/会话/封禁/审计/访问统计，无 `/api/admin/*`；`server/auth.py`、`server/state.py` 删除。配置唯一来源是 `config.json`（改文件后重启生效）。前端仅保留公开只读监控（设备卡片、健康角标、历史时间线、SSE/轮询）。

**Verification** — `python -m pytest -q`：**48 passed**（系统 Python 3.14）。首轮独立评审 2 个 CRITICAL（403/envelope 40100 未按鉴权处理、历史代理鉴权契约缺口）已修复；复审确认两项均解决，无新增 CRITICAL，[S2] 鉴权契约 PASS。

**Journey log** —
1. 上游收敛为单 `api_token` 后，WebUI 的 `viewer_token` 必须整体改名并在配置校验层拒绝旧键，避免静默读不到 token。
2. 鉴权失败是三条路径（HTTP 401、HTTP 403、envelope `code=40100`），只测 401 会“全绿”却漏契约。
3. 历史代理原先 `raise_for_status()` + 空 data 兜底，会把鉴权失败变成 502 或假空历史；需先判鉴权再兜底。
4. SSE 测试用 `TestClient.stream` 会挂死（生成器不结束）；改为断言路由注册即可。
5. 用户指定直接在 `main` 上按文件提交（覆盖 compose-next 默认 worktree）。

## [S1] Problem

上游 WhatTheManDoing 已收敛为全站唯一 `api_token`（Bearer，读写共用），并固定路径前缀 `/api/v1`；旧的 `viewer_token` / 多 Token 模型不再存在。WebUI 仍按 `viewer_token` 取数，鉴权失败信息也不匹配新契约。

同时用户明确要求：网页**不设后台**，设备与 WebUI 配置**只能**通过配置文件修改。现有登录、会话、封禁、审计、访问统计、管理 API 与管理面板均应拆除，避免无入口的死代码与攻击面。

## [S2] Design

### 决策（已确认）

| 轴 | 选择 |
|----|------|
| 工作区 | 直接在 `main` 上按文件提交（用户指定，覆盖 worktree 默认） |
| 设备配置模型 | **沿用逐设备条目**（id/name/api_base_url/api_token/enabled） |
| 后台 | **完全移除**网页管理后台；配置仅 `config.json` |

### 上游接入契约（对齐 docs/API.md v1）

- `DeviceConfig.viewer_token` **改名为** `api_token`；请求头 `Authorization: Bearer <api_token>`。
- `api_base_url` 仍指向 API 根（默认 `http://127.0.0.1:8765/api/v1`），子路径不变：
  - 轮询：`GET {base}/devices/{id}` → 失败则 `{base}/devices` → `{base}/status`
  - 历史：`GET {base}/devices/{id}/history?limit=`，404 时回退 `{base}/status/history?limit=`
  - 探活：`GET {base}/health`（无需鉴权，仅连通性用；业务取数仍走带 Token 路径）
- Token 为空：不发 `Authorization` 头；上游空 `api_token` 会 401，快照记 `unauthorized (api_token?)`。
- 401/403 与 envelope `code=40100` 一律视为鉴权问题，错误文案指向 `api_token`。
- 保持 envelope 解析：`code==0` 且 `data` 含设备对象；空/未命中 payload **不**伪造在线（沿用既有修复）。

### 仅配置文件

- `config.json` 为唯一配置源：启动 `ensure_config`（缺失则默认创建）；**运行中不提供任何写配置 API**。改配置 = 编辑文件 + 重启进程。
- 删除配置键：`admin_token`、`session_ttl_seconds`、`login_max_failures`、`ban_duration_hours`、`login_rate_limit_per_minute`、`default_device_scheme`。
- 保留配置键：`version`、`refresh_interval_seconds`、`page_title`、`page_subtitle`、`show_history`、`devices_per_page`、`trust_proxy`、`forwarded_allow_ips`、`serve.*`、`log.*`、`devices[]`。
- `devices[]` 字段：`id`、`name`、`api_base_url`、`api_token`、`enabled`（严格校验沿用：id 正则、URL 必须 http(s)、enabled 必须 bool）。
- 示例配置与 README 同步为 `api_token`；旧键 `viewer_token` **不再识别**（文件侧直接改名，无兼容垫片）。

### 移除的子系统

| 移除 | 说明 |
|------|------|
| `/api/admin/*` | 登录/登出/me、config 读写、设备 CRUD/导出/测试、stats/bans/audit/logs |
| `server/auth.py` | 会话、封禁、登录限流；`extract_client_ip` 内联进 `main.py` 供访问日志使用 |
| `server/state.py` + `state.json` | 访问计数、登录失败、封禁、审计均无展示入口 |
| `server/devices.py` 中 CRUD | `upsert_device` / `delete_device` / `test_device_connection` / `apply_scheme`；仅保留 `fetch_device_history` |
| 前端管理面板 | 登录、设置、设备管理、统计、封禁、审计、日志入口与相关 DOM/样式 |

### 保留的公开能力

- `GET /`、`/styles.css`、`/app.js` 静态页。
- `GET /api/public/config`（标题/副标题/刷新间隔/show_history）。
- `GET /api/public/probe`（反代/穿透诊断，保留）。
- `GET /api/public/devices`、`/api/public/devices/{id}/history`、`GET /api/public/stream`（SSE）。
- 设备健康角标、空/错误态、历史、深浅色、reduced-motion 等公开 UX 不变。

### 错误与边界

- 上游 401：设备 `error="unauthorized (api_token?)"`，`online=false`。
- 上游 429：记 `HTTP 429`，不伪造状态。
- 配置校验失败：启动即抛 `ConfigError`（与现行为一致）。
- 中间件仅保留访问日志与耗时；不再写 `state.json`、不再做封禁拦截。

### 测试边界

- TestClient + DummyAggregator / mock fetcher；**不**启动 uvicorn 真进程。
- 删除 login/session/ban/audit/admin CRUD 用例。
- 新增/改写：`api_token` 请求头、空 token 不带头、401 文案、配置键 `api_token` 校验与 `viewer_token` 拒绝、公开 API 不依赖后台。
- 全量 `python -m pytest` 通过。

## [S3] Out of Scope

- 不改上游 WhatTheManDoing。
- 不做配置热重载 / 文件监听。
- 不做多管理员、OAuth、Token 轮换。
- 不把 WebUI 做成可写控制台。
- 不在本轮做 HTTPS 证书自动申请或穿透工具配置片段。

## Tasks

- [x] T1: `config.py` 去掉后台字段并改用 `api_token` — acceptance: 校验通过新字段；`viewer_token`/`admin_token` 等不再接受 (covers: S2)
- [x] T2: `aggregate.py` 以 Bearer `api_token` 对接 v1 路径 — acceptance: 请求头与 401 文案更新；空 token 不发鉴权头 (covers: S2)
- [x] T3: `devices.py` 只保留 history 代理并用 `api_token` — acceptance: 无 CRUD/测试连接；历史回退 `/status/history` (covers: S2)
- [x] T4: `main.py` 拆除 `/api/admin/*` 与 state/auth 依赖 — acceptance: 仅公开路由；IP 解析仍用于日志 (covers: S2)
- [x] T5: 删除 `auth.py`、`state.py` 及 state 测试 — acceptance: 仓库无会话/封禁/审计代码 (covers: S2)
- [x] T6: 前端移除管理 UI（html/js/css） — acceptance: 无登录/管理入口；公开监控可用 (covers: S2)
- [x] T7: 测试改为公开 API + `api_token` 契约 — acceptance: `python -m pytest` 全绿 (covers: S2)
- [x] T8: 同步 `config.example.json` / README / DESIGN.md — acceptance: 文档描述仅文件配置与 v1 Token (covers: S1, S2)

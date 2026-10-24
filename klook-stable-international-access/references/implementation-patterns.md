# Klook 稳定访问实现模式

## 目录

1. [访问层边界](#访问层边界)
2. [配置模型](#配置模型)
3. [持久浏览器与 Profile 锁](#持久浏览器与-profile-锁)
4. [国际站 Cookie 规范化](#国际站-cookie-规范化)
5. [域名观察与结果校验](#域名观察与结果校验)
6. [页面状态分类](#页面状态分类)
7. [查询租约与生命周期](#查询租约与生命周期)
8. [重试、限流与熔断](#重试限流与熔断)
9. [Clash/代理规则](#clash代理规则)
10. [诊断与敏感信息](#诊断与敏感信息)
11. [测试与上线检查表](#测试与上线检查表)

## 访问层边界

把浏览器稳定性封装成一个可复用客户端，业务调用方只获得：

- `start()`：启动或复用 Context/Page；
- `query_page(url)`：在串行限频租约内完成一次完整查询；
- `check_session_state()`：在滚动、翻页和解析期间检测晚到的跳转/风控；
- `resume_after_manual_verification()`：人工完成 L1 后做健康检查并恢复；
- `close()`：队列空闲时正常关闭浏览器但保留 Profile。

访问层负责浏览器、Profile、代理、`.com` 约束、状态分类、限频、熔断和诊断；不负责 POI 选择、价格解析、分页口径、任务抢占或数据库状态。

## 配置模型

至少提供以下显式配置，环境变量名可以按项目规范调整：

```text
profile_dir                         每 worker 唯一且持久的目录
artifact_dir                        诊断截图和最小元数据目录
proxy_server                        http/https/socks5 完整地址，必填
proxy_username / proxy_password     可选；日志永不输出
headless                            默认 false
browser_channel                     默认 chrome
minimum_query_interval_seconds      默认且最小 30
query_jitter_seconds                默认 5，实际随机 0..5
navigation_timeout_seconds          例如 120
automatic_interstitial_timeout      例如 30
manual_verification_timeout         0 表示保持浏览器并无限等待人工，或按运维 SLA 配置
healthy_observation_seconds         例如 2，防止瞬时 NORMAL 误判
```

启动时校验：

- 缺少显式代理直接失败，不读取系统 VPN 状态作回退。
- Profile 路径不得为空、临时随机或被多个 worker 配成相同目录。
- 最小间隔低于 30 秒时拒绝启动，除非已经有新的专项压测结论并同步更新 Skill。
- 代理日志只显示 `scheme://host:port`，不显示用户名和密码。

## 持久浏览器与 Profile 锁

Python/Playwright 结构示意：

```python
launch_options = {
    "user_data_dir": str(profile_dir),
    "channel": "chrome",
    "headless": False,
    "args": ["--disable-blink-features=AutomationControlled"],
    "viewport": {"width": 1440, "height": 900},
    "proxy": {"server": proxy_server},
}
context = await playwright.chromium.launch_persistent_context(**launch_options)
page = context.pages[0] if context.pages else await context.new_page()
```

这只是启动骨架。稳定性来自完整组合，不能把 `AutomationControlled` 参数单独宣传为“防封”。

在 Profile 同级目录创建 advisory lock：

- macOS/Linux 使用 `fcntl.flock(..., LOCK_EX | LOCK_NB)`；
- Windows 使用 `msvcrt.locking(..., LK_NBLCK, 1)`；
- 锁文件写入 PID、获取时间和 Profile 路径，便于诊断；
- 获取失败抛出明确错误，不要自动生成临时 Profile；
- 关闭 Context/Playwright 后释放锁。

浏览器崩溃时可以关闭残余对象后用相同目录重启。不要删除 `Singleton*` 或 Profile 文件，除非已经确认没有活跃 Chrome 且有专门的崩溃恢复设计。

## 国际站 Cookie 规范化

在首次启动后和每次导航前执行幂等函数。伪代码：

```python
cookies = await context.cookies()

if any(c["name"] == "klk_rdc" for c in cookies):
    await context.clear_cookies(name="klk_rdc")

site_cookies = [c for c in cookies if c["name"] == "klk_ps"]
canonical = (
    len(site_cookies) == 1
    and site_cookies[0]["domain"] == ".klook.com"
    and site_cookies[0]["path"] == "/"
    and site_cookies[0]["value"] == "1"
)

if not canonical:
    if site_cookies:
        await context.clear_cookies(name="klk_ps")
    await context.add_cookies([{
        "name": "klk_ps",
        "value": "1",
        "domain": ".klook.com",
        "path": "/",
        "secure": True,
        "httpOnly": False,
        "sameSite": "Lax",
        "expires": now_plus_one_year,
    }])
```

注意：

- 使用项目 Playwright 版本支持的定向 `clear_cookies` 参数；若不支持，读取并重建非目标 Cookie 前必须评估安全性，不能直接全清。
- host-only `klk_ps` 与 domain cookie 同时存在时，必须先清全部同名 Cookie。
- 不要删除 DataDome Cookie、LocalStorage、IndexedDB 或缓存。
- 客源地应由业务层另行设置 `transfer-user-residence=1_<CC>` 或 `x-klook-user-residence: <CC>`，不要混进国际站规范化函数。

## 域名观察与结果校验

入口 URL 先做严格校验：

- scheme 必须为 `https`；
- hostname 只允许 `klook.com`、`www.klook.com` 或项目明确审定的国际站子域；
- 禁止 URL 用户名、密码和非预期端口。

导航前注册被动观察器：

```python
def observe_request(request):
    host = urlsplit(request.url).hostname
    path = urlsplit(request.url).path.lower()
    if is_klook_cn(host) and request.is_navigation_request():
        open_domain_circuit(request.url)
    if "/transfercarrentalapisrv/" in path and not is_expected_com_host(host):
        open_domain_circuit(request.url)

page.on("request", observe_request)
```

不要为了审计而使用 `context.route("**/*", ...)`；Playwright 注册 Route 会关闭相应 Context 的 HTTP 缓存，削弱持久 Profile 的缓存连续性。只有确需修改/阻止请求时才使用 Route。

一次查询完成前做三次检查：

1. `goto` 稳定后验证最终主页面 URL。
2. 滚动、翻页或等待 API 期间定期检查页面/Frame 状态和已观察请求。
3. 解析结束、释放查询租约前再次检查。

发现 `.cn` 后不要保存本次响应。记录第一个违规 URL，打开 session 熔断；后续排队协程拿到租约后必须再次检查熔断状态，避免它们在排队前已经通过一次旧检查。

## 页面状态分类

读取主页面及所有 Frame 的有限信息：URL、标题和空白压缩后的正文前 4,000 字符。Frame 可能在过渡时 detach，读取失败应容忍并继续使用其他 surface。

分类优先级建议：

1. 任一 surface 位于 `klook.cn` → `DOMESTIC_REDIRECT`。
2. 命中明确 hard-block 文案 → `L2_HARD_BLOCK`。
3. Frame URL 为 `captcha-delivery.com/captcha` 或命中滑块文案 → `L1_SLIDER`。
4. Frame URL 为 `captcha-delivery.com/interstitial` → `AUTO_INTERSTITIAL`。
5. 主页面位于健康 `.com` 且关键状态正常 → `NORMAL`。
6. 其他 → `UNKNOWN`。

不要用裸 `403`、裸 `ID: <uuid>` 或“页面里出现 DataDome”判定 L2。滑块和自动过渡都可能带 403/诊断 ID。

`RATE_LIMITED` 通常来自 HTTP 429、业务响应码或明确 Too Many Requests 文案，作为响应层状态补充到同一动作状态机。如果它同时命中 L1/L2 页面证据，选择更严格状态。

自动过渡处理：

- 在当前页面轮询最多约 30 秒；不刷新。
- 要求页面连续健康约 2 秒且最终主导航状态 `< 400` 再返回 `NORMAL`。
- 超时仍不健康时归为 `UNKNOWN` 并暂停。

L1 处理：

- 立即截图和告警；停止所有新导航。
- 多个 worker 共用代理出口时，按脱敏 `exit_id` 发布共享暂停，停止该出口上的其他 Klook worker。
- 保持有头浏览器在原验证页。
- 只轮询状态，不点击、拖动或刷新。
- 人工处理后要求最终页面为允许的 `.com` 且状态稳定，才清除熔断。

L2 处理：

- 立即截图并打开熔断。
- 多个 worker 共用代理出口时同步打开出口级熔断，避免其他会话继续请求。
- 不自动恢复、不刷新、不切换 Profile。
- 由运维检查出口、IP 信誉、近期并发/频率和 Profile，再显式恢复或更换合规出口。

## 查询租约与生命周期

用一个异步锁同时实现串行和最小间隔：

```python
async with rate_limiter:
    ensure_circuit_closed()
    normalize_international_cookies()
    result = await navigate_and_settle(url)
    yield result, page       # 业务层在租约内滚动、收集、解析
    await check_session_state()
# __aexit__ 在完整查询结束时记录 finished_at
```

下一次查询开始时间应满足：

```text
last_query_finished_at + minimum_interval + uniform(0, jitter)
```

这样慢查询不会因为“从上次开始已过 30 秒”而紧接着发起下一次请求。

Worker 生命周期：

```text
启动 worker
  -> 获取 Profile 锁并启动浏览器
  -> 抢到任务：复用当前浏览器执行
  -> 再次抢任务：有任务则继续复用
  -> 队列为空：正常关闭浏览器并释放锁
  -> 保留 Profile，等待下一次 worker/批次启动
```

如果调度器每个任务都新建 Python 子进程，至少保证整个子进程内所有 URL/日期/客源地复用一个 Browser Session。更理想的是把浏览器所有权放在持续运行的 worker 内。

## 重试、限流与熔断

建立明确矩阵：

| 异常 | 浏览器动作 | 等待 | 次数 |
| --- | --- | --- | --- |
| 瞬时网络/Playwright timeout，页面无敏感状态 | 保持 Context，同页重试 | 15/30/45 秒等有界退避 | 最多 3 次 |
| Context/浏览器确认崩溃 | 同一 Profile 重启 | 启动退避 | 最多 3 次 |
| HTTP 429 / Too Many Requests | 不重启，设置 worker/出口冷却 | `Retry-After`；否则 60/120/240 秒 | 最多 3 次 |
| `AUTO_INTERSTITIAL` | 原页等待 | 有界约 30 秒 | 不算普通重试 |
| `L1_SLIDER` | 保持原页等待人工 | 按运维 SLA 或无限等待 | 不做普通重试 |
| `L2_HARD_BLOCK` | 熔断并保留现场 | 人工处置 | 0 次 |
| `DOMESTIC_REDIRECT` | 熔断并拒绝数据 | 修复 Cookie/出口后显式恢复 | 0 次 |
| `UNKNOWN` | 保存诊断并暂停 | 人工/程序版本排查 | 0 次 |

解析 `Retry-After` 时同时支持秒数与 HTTP-date。若服务端给出的时间短于内部安全冷却，取更保守值。冷却状态放在 Browser Session 之外或持久 worker 状态中，使浏览器重启不能绕过。多个 worker 共用出口时，以脱敏 `exit_id` 共享 L1/L2 熔断和 429 冷却，不要记录代理凭据作为标识。

不要对“没有业务结果”自动重启浏览器。先区分正常空结果、页面未触发业务 API、429、风控和解析字段变化。

## Clash/代理规则

示意规则，实际策略组名按环境替换，并放在 GEOIP/DIRECT 之前：

```yaml
rules:
  - DOMAIN-SUFFIX,klook.com,KLOOK_FIXED
  - DOMAIN-SUFFIX,captcha-delivery.com,KLOOK_FIXED
  - DOMAIN-SUFFIX,datadome.co,KLOOK_FIXED
  # ...其他规则...
  - GEOIP,CN,DIRECT
```

`KLOOK_FIXED` 必须固定到同一节点/出口，不应是会在查询中自动切换国家的负载均衡组。浏览器配置的 `proxy_server` 可以指向本机 Clash HTTP/SOCKS 端口，但仍需保证该入口内部不会把 Klook 与 DataDome 分流。

验收时检查代理连接日志，而不是只访问一个“我的 IP”页面。至少核对 Klook 主站、租车业务 API、`captcha-delivery.com` 和实际出现的 DataDome 子域都命中 `KLOOK_FIXED`。同时检查 IPv6 是否绕过。

## 诊断与敏感信息

每次非正常状态保存一个有限诊断包：

```json
{
  "captured_at": "UTC ISO-8601",
  "state": "L1_SLIDER",
  "reason": "matched captcha frame",
  "requested_url": "https://www.klook.com/...",
  "final_url": "https://www.klook.com/...",
  "http_status": 403,
  "matched_marker": "/captcha/",
  "surfaces": [
    {"label": "frame[1]", "url": "https://...captcha-delivery.com/captcha/...", "title": "...", "body_excerpt": "bounded text"}
  ],
  "proxy": "http://127.0.0.1:PORT",
  "screenshot_path": "..."
}
```

执行以下脱敏：

- URL 查询参数可能含 Token/搜索标识时只保留允许字段或整体打码。
- 代理只保留 scheme/host/port。
- 正文摘录限制长度，并避免保存完整车辆结果。
- 不读取或记录 Cookie 值、LocalStorage、Authorization、代理凭据和完整 HTML。
- 截图可能包含业务数据，设置访问控制和保留期限。

## 测试与上线检查表

### 单元测试

- 配置缺少代理、Profile 重复、间隔过短时 fail fast。
- Cookie 已规范、存在 `klk_rdc`、重复 `klk_ps`、host-only `klk_ps` 四种场景。
- URL allowlist，包含伪造子域、用户信息和端口的负例。
- 七种页面/响应状态的夹具分类。
- 裸 403 和裸 DataDome ID 不被误判为 L2。
- 排队协程在获取查询锁后重新检查熔断。
- `Retry-After` 秒数/日期解析和 60/120/240 秒回退。
- 日志不包含代理密码、Cookie 或 Token。

### 集成测试

- 使用一个干净且独立的 Profile 访问租车 `.com` 页面，关闭再启动仍为 `.com`。
- 人工构造 `klk_rdc=CN`/重复 `klk_ps`，验证定向修复，不全清 Cookie。
- 观察关键租车 API 域名；任何 `.cn` 请求都会中止并拒绝数据。
- 测试浏览器复用多个查询，确认只启动一次、查询串行且结束间隔至少 30 秒加抖动。
- 用本地安全夹具模拟 L1/L2/429，不对真实 Klook 制造风控压力。
- 人工 L1 测试时确认浏览器保留、程序无点击/刷新、验证完成后健康检查才恢复。

### 环境验收

- 每个实例使用不同 Profile 路径和锁。
- 服务器有可见桌面/虚拟显示环境支持有头 Chrome；不要悄悄降级为 headless。
- 显式代理可达且固定；DNS、IPv4/IPv6、Klook/DataDome 出口一致。
- 告警能通知 L1/L2/`.cn`/连续 429，并包含安全的诊断路径。
- 浏览器崩溃恢复继续使用同一 Profile；队列空闲后正常关闭并保留 Profile。
- 小流量灰度持续观察后再扩容；扩容按出口总并发计算，不只看单实例间隔。

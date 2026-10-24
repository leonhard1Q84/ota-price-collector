---
name: klook-stable-international-access
description: 为所有 Klook 网页自动化程序设计、实现、审查和诊断稳定的国际站（.com）访问层，覆盖 Playwright 有头持久 Profile、显式固定代理、.cn 跳转治理、Klook/DataDome 同出口、AUTO_INTERSTITIAL/L1/L2 风控分级、429/Too Many Requests 退避、浏览器生命周期、限频、诊断与验收。用户提到 Klook 国际站/国内站、klook.com/klook.cn、DataDome、captcha-delivery、滑块、403/二级风控、持久 userdata/Profile、Playwright/CDP/headless、VPN/Clash/DIRECT、代理出口或 Too Many Requests 时使用。
---

# Klook 国际站稳定访问

把 Klook 访问能力实现为独立基础设施层，再在它之上开发价格、评分、覆盖率等业务逻辑。目标是稳定停留在 `.com`、降低触发风控的概率，并在异常出现时停止放大风险；不要承诺“绝不触发风控”。

## 读取参考资料

- 解释跳转、VPN、Profile、DataDome 或风控原因时，完整读取 [causes-and-evidence.md](references/causes-and-evidence.md)。
- 新建、修改或审查程序时，完整读取 [causes-and-evidence.md](references/causes-and-evidence.md) 和 [implementation-patterns.md](references/implementation-patterns.md)。
- 不要把价格中心特有的 POI 下拉选择、任务表或报价解析逻辑加入此通用访问层。

## 坚持稳定基线

默认采用已经过本地压力观察和真实部署验证的组合：

1. 使用普通 Playwright `launch_persistent_context` 启动有头 Google Chrome；不要求 CDP。
2. 为每个 worker 配置独立、长期保留的持久 Profile，并使用跨进程锁禁止并发共享。
3. 在浏览器上下文中显式配置固定代理；不要依赖服务器系统 VPN 或自动分流。
4. 确保 Klook、DataDome、`captcha-delivery.com`、DNS、IPv4 与 IPv6 使用一致出口。
5. 串行查询；默认在上一次完整查询结束后至少静默 30 秒，再增加 0–5 秒随机抖动。
6. 同一批任务持续复用浏览器和 Profile。只有队列空闲后才正常关闭浏览器；Profile 永久保留。
7. 只在浏览器或上下文真实损坏时重启，并继续使用同一 Profile。不要把重启当作页面重试手段。

把 30 秒加抖动视为当前已验证的保守起点，而不是 Klook 公布的固定阈值。增加并发或缩短间隔前必须重新压测并观察至少两级风控和 429。

## 按顺序实施

### 1. 盘点现状

确认以下事实后再改代码：

- 浏览器由 Playwright 启动还是通过 CDP 连接；是否有头；使用 Chromium 还是系统 Chrome。
- `user_data_dir` 是否稳定、是否被多个进程共享、是否在每次任务后删除。
- 代理是否直接传给浏览器上下文；代理程序内部是否仍可能按域名 `DIRECT`。
- 浏览器是否每个 URL 都开关；查询是否并发；重试是否通过刷新或重启实现。
- 是否已经处理 `klk_ps`、`klk_rdc`、最终域名、租车业务 API 域名和页面状态。

先保留现有 Profile。不要把“删除 userdata”作为默认排障步骤。

### 2. 建立单一浏览器所有权

- 一个 worker 只拥有一个持久 Context 和一个复用 Page。
- 一个 Profile 同时只允许一个进程持有；拿不到锁时直接失败，不要回退到临时 Profile。
- 优先使用已安装的稳定版 Google Chrome channel 和有头模式。
- 可以保留已验证的 `--disable-blink-features=AutomationControlled`，但不得把它描述成规避风控的充分条件。
- 不要复制个人浏览器 Profile 或在机器间共享 Profile；每台机器、每个 worker 独立初始化。

### 3. 规范化国际站状态

每次导航前执行幂等的定向修复：

1. 只允许输入 `https://klook.com` 或 `https://www.klook.com` URL。
2. 清除所有名为 `klk_rdc` 的路由 Cookie，尤其是残留的 `klk_rdc=CN`。
3. 检查 `klk_ps` 是否恰好只有一个规范值：域为 `.klook.com`、路径为 `/`、值为 `1`。
4. 若不规范，只清除所有 `klk_ps` 后重新写入该规范 Cookie。
5. 保留 DataDome Cookie、缓存和其他长期 Profile 状态；不要全量清 Cookie。

区分两类状态：`klk_ps/klk_rdc` 控制站点路由；`transfer-user-residence=1_<国家码>` 和 `x-klook-user-residence` 控制客源地/用户居住地。后者不能替代 `.com` 站点控制。

导航完成后同时校验：

- 主页面最终 URL 仍在 Klook `.com` 允许列表内；
- 关键 Klook 租车业务 API 请求来自预期 `.com` 域名；
- 任一页面、Frame、导航请求或业务 API 转到 `.cn` 时拒绝数据、保存诊断并打开熔断。

使用被动 `request` 观察器完成域名审计。除非业务确实需要拦截请求，不要仅为观察而注册 Playwright Route，因为它会影响 Context 的 HTTP 缓存行为。

### 4. 固定完整网络路径

- 把代理直接传给 Playwright Context，并在缺少代理时 fail fast。
- 在 Clash 等代理中把 `klook.com`、`captcha-delivery.com`、`datadome.co` 放入同一个固定节点/策略组，规则置于 `GEOIP,CN -> DIRECT` 等兜底规则之前。
- 检查相关域名的 DNS、IPv4 和 IPv6 是否也遵循同一策略，避免一部分直连、一部分代理。
- 不要把“VPN 图标已打开”“全局模式已选”当作程序出口证据；通过代理日志或脱敏出口探针验收。

把出口一致性作为启动硬门禁，而不是普通风险提示。在本项目的重复测试环境中，Klook 主站走中国直连、DataDome/`captcha-delivery.com` 走日本代理时会稳定进入 `L2_HARD_BLOCK`，不应归为可能触发 L1 的概率因素。预检发现相关域名实际出口不一致时直接判定环境不可运行，禁止先请求 Klook 再观察结果。同出口是避免这一类稳定 L2 的必要条件，但不是永不触发风控的充分条件；该判断是项目实测工程结论，不是 DataDome 面向所有站点和版本公开的通用保证。

### 5. 管理生命周期和频率

把限频租约覆盖一次完整查询：导航、等待页面稳定、滚动/翻页、业务 API 收集和解析全部完成后，才开始计算下一次静默间隔。

- 默认串行执行，不在同一出口上启动多个并行 Page 抢吞吐。
- 有任务时复用现有浏览器；抢不到新任务或队列确认空闲后才关闭。
- 正常关闭 Context 以写回 Profile；下次任务仍使用同一目录。
- 不要快速刷新、连续 `goto`、频繁新建 Context，或通过开关浏览器绕过冷却。

### 6. 使用状态机而不是把所有 403 当成同一错误

至少区分下列状态，并按表处理：

| 状态 | 识别含义 | 必须动作 |
| --- | --- | --- |
| `NORMAL` | 页面和关键业务请求均为健康 `.com` | 继续查询 |
| `AUTO_INTERSTITIAL` | DataDome 自动过渡页 | 原地等待一个有界窗口，不刷新；恢复为 `NORMAL` 后继续 |
| `L1_SLIDER` | 滑块/人机验证 | 暂停该 worker；共享同一出口时暂停该出口的全部 worker。保留浏览器与 Profile，告警并等待人工完成 |
| `L2_HARD_BLOCK` | 明确的风险拦截/自动化访问被拒绝页面 | 立即熔断；共享同一出口时同步阻止新请求。保留页面、Profile 和诊断；禁止普通重试 |
| `DOMESTIC_REDIRECT` | 页面、Frame、导航或租车 API 命中 `.cn` | 拒绝本次数据并熔断，定向检查路由 Cookie 与出口 |
| `RATE_LIMITED` | HTTP 429 或明确 Too Many Requests | 保持会话，按 `Retry-After` 或长退避冷却，最多重试 3 次 |
| `UNKNOWN` | 无法安全归类 | 保存诊断并暂停，不得当作成功 |

403 可能对应自动过渡、L1 或 L2；不得只根据 HTTP 状态判定。检查主页面和所有 Frame 的 URL、标题及有限正文标记。

不要自动拖动滑块、破解验证码、自动刷新验证页或用 Selenium/无痕模式尝试“绕过”。人工完成 L1 后，只有重新检查到健康 `.com` 页面才能关闭熔断。

多个 worker 共用一个代理出口时，用共享的 `exit_id` 协调 L1/L2 熔断和 429 冷却；只暂停单个进程可能让其他进程继续放大同一出口的风险。

### 7. 分离三种重试

- 网络波动或浏览器崩溃：在相同 Profile 下做有界重试；仅当 Context 已损坏时重启浏览器。
- `RATE_LIMITED`：优先遵循 `Retry-After`；缺失时使用 60、120、240 秒退避，最多重试 3 次。冷却属于 worker/出口，不能因浏览器重启而清零。
- `L1_SLIDER`、`L2_HARD_BLOCK`、`DOMESTIC_REDIRECT`：不进入普通三次重试；分别等待人工或直接熔断。

直接调用 Klook 数据接口并不会天然绕过 DataDome 或限流。除非已有受支持的正式接口，否则优先让健康页面产生业务请求并被动读取响应，不要伪造会话或用接口直连规避页面风控。

### 8. 保存最小诊断证据

异常时至少记录：时间、请求 URL、最终 URL、页面标题、主导航 HTTP 状态、页面状态、命中标记、有限 Frame 摘要、截图路径、脱敏代理位置及相关请求的出口一致性结果。

不得记录或提交 Cookie、Token、完整 HTML、代理密码、浏览器 Profile 内容或完整业务 Raw Payload。截图也要按内部数据安全要求管理。

## 验收

在不触发生产副作用的测试任务中完成以下检查：

1. 全新独立 Profile 首次访问 `.com` 成功，之后重启仍保持 `.com`。
2. 同一 Profile 先前存在 `.cn` 路由状态时，定向修复后可恢复 `.com`，且 DataDome 状态未被全量删除。
3. 国内与国际出口环境下都以站点 Cookie 和最终域名为准；不把 IP 国别当作唯一判断。
4. 代理日志证明 Klook 和 DataDome 相关域名出口一致；构造出口不一致的预检夹具时，程序会在访问 Klook 前直接失败，不会把它归为 L1 后继续运行。
5. 连续多次真实查询遵守串行与最小间隔，浏览器不随 URL 频繁开关。
6. 通过夹具或安全测试页验证七种状态的分类和动作；L1/L2/`.cn` 不被普通重试。
7. 429 测试遵循 `Retry-After` 或 60/120/240 秒退避，且重启浏览器不会清除冷却。
8. 日志和诊断产物不泄露凭据、Cookie 或完整页面数据。

交付时明确说明：采用了哪些基线、哪些仍是概率性风险、如何人工处理 L1、如何恢复 L2/`.cn` 熔断，以及实际完成了哪些验收项。

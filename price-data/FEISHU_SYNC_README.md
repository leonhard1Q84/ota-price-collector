# 飞书价格文件同步说明

这个工具会扫描本地 `price_data` 目录，把文件按文件名前缀代码同步到对应的飞书 Wiki 子文档中。

## 同步规则

- `FUK_BM20260506_140211.csv` 会进入 `FUK` 文档。
- `BKS_filtered_data_20260512_142215.csv` 会进入 `BKS` 文档。
- `SMV_BM20260512_151621.xlsx` 会进入 `SMV` 文档。
- 文档内部按日期标题分组，例如 `2026-05-12`。
- 第一次发现文件时，会在对应日期标题下插入附件。
- 本地文件内容更新后，会用新的附件替换原来的附件块。
- 已同步且未变化的文件会跳过，状态保存在 `src/feishu_sync_state.json`。

## 飞书准备

1. 在飞书开放平台创建自建应用。
2. 获取应用的 `App ID` 和 `App Secret`。
3. 给应用开通云文档、知识库、素材上传、文档编辑相关权限。
4. 把应用加入目标 Wiki，并给它编辑权限。
5. 目标 Wiki 链接已经写在配置模板中：

```text
https://mcnxfjx6aasb.feishu.cn/wiki/Zvqlwrh2Airea1k8RIbcb1Shnnc?from=from_copylink
```

## 配置

复制配置模板：

```powershell
Copy-Item src\feishu_sync_config.example.json src\feishu_sync_config.json
```

设置当前 PowerShell 窗口的飞书凭证：

```powershell
$env:FEISHU_APP_ID="你的 App ID"
$env:FEISHU_APP_SECRET="你的 App Secret"
```

默认扫描两个目录：

```text
F:\klook\src\price_data
C:\Users\42047\PycharmProjects\price_data\src\price_data
```

如果 Wiki 下已经有 `FUK`、`BKS`、`SMV` 这种同名子文档，脚本会自动匹配。没有同名子文档时，默认会自动创建新版文档。

## 运行

先只扫描，不写飞书：

```powershell
run_feishu_sync.bat --dry-run
```

正式同步：

```powershell
run_feishu_sync.bat
```

持续监听本地更新，每 5 分钟同步一次：

```powershell
run_feishu_sync.bat --watch --interval 300
```

只同步前 10 个待处理文件，适合首次试跑：

```powershell
run_feishu_sync.bat --limit 10
```

## 推荐上线方式

首次使用建议先执行：

```powershell
run_feishu_sync.bat --dry-run --limit 20
run_feishu_sync.bat --limit 3
```

确认飞书文档格式正确后，再执行完整同步。后续可以用 Windows 任务计划程序每隔 5 或 10 分钟运行一次 `run_feishu_sync.bat`。

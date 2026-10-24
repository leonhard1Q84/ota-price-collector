# -*- coding: utf-8 -*-
"""飞书通知配置模板。

复制为 feishu_notify_config.py 后填写真实值。
"""

FEISHU_APP_ID = ""
FEISHU_APP_SECRET = ""
FEISHU_NOTIFY_CHAT_ID = ""

# 可选：需要在群通知中 @ 指定人员时填写。
FEISHU_NOTIFY_AT_USER_ID = ""
FEISHU_NOTIFY_AT_USER_NAME = "运营同学"

# 告警专用群：运行错误、登录失效、风控、任务中断等通知使用此群。
FEISHU_ALERT_CHAT_ID = ""
FEISHU_ALERT_AT_USER_ID = ""
FEISHU_ALERT_AT_USER_NAME = "Jiangyi"
FEISHU_ALERT_LARK_CLI_PROFILE = ""
# FEISHU_ALERT_LARK_CLI_PATH = "/absolute/path/to/lark-cli"

# 可选：只想发送文字提醒时可配置 webhook。配置了 App 信息时优先使用 App。
FEISHU_WEBHOOK_URL = ""

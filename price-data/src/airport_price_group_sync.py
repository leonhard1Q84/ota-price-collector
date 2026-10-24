import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    from feishu_sync import BASE_URL, FeishuClient, FeishuError, sha256_file
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("请从 price_data 项目的 src 目录运行本脚本") from exc

try:
    import feishu_notifier
except ImportError:  # pragma: no cover - the sync script can still report locally.
    feishu_notifier = None


DEFAULT_CONFIG = Path(__file__).with_name("airport_price_group_sync_config.json")
DEFAULT_SCAN_DATE = datetime.now().strftime("%Y%m%d")


@dataclass
class AirportPriceFile:
    path: Path
    project: str
    source: str
    size: int
    mtime_ns: int

    @property
    def key(self) -> str:
        return str(self.path.resolve()).lower()

    @property
    def signature(self) -> str:
        return f"{self.size}:{self.mtime_ns}"


class AirportGroupSyncState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, Any] = {"version": 1, "files": {}, "chat": {}}

    def load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as file:
            self.data = json.load(file)
        self.data.setdefault("version", 1)
        self.data.setdefault("files", {})
        self.data.setdefault("chat", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def already_synced(self, item: AirportPriceFile) -> bool:
        record = self.data["files"].get(item.key)
        return bool(record and record.get("signature") == item.signature and record.get("status") == "sent")

    def mark_sent(self, item: AirportPriceFile, values: Dict[str, Any]) -> None:
        record = dict(values)
        record.update(
            {
                "path": str(item.path.resolve()),
                "project": item.project,
                "source": item.source,
                "size": item.size,
                "mtime_ns": item.mtime_ns,
                "signature": item.signature,
                "sent_at": datetime.now().isoformat(timespec="seconds"),
                "status": "sent",
            }
        )
        self.data["files"][item.key] = record


def resolve_env_value(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    return text


def resolve_path(value: str, project_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def load_config(path: Path) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    with path.open("r", encoding="utf-8") as file:
        config = json.load(file)
    config["app_id"] = resolve_env_value(config.get("app_id")) or os.getenv("FEISHU_APP_ID", "")
    config["app_secret"] = resolve_env_value(config.get("app_secret")) or os.getenv("FEISHU_APP_SECRET", "")
    config["state_file"] = str(resolve_path(config.get("state_file", "src/airport_price_group_sync_state.json"), project_root))
    config["poll_interval_seconds"] = int(config.get("poll_interval_seconds") or 60)
    config["extensions"] = {str(ext).lower() for ext in config.get("extensions") or [".xlsx", ".csv"]}
    return config


def _file_from_path(path: Path, project_name: str, source: str) -> Optional[AirportPriceFile]:
    if not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    if stat.st_size <= 0:
        return None
    return AirportPriceFile(path=path.resolve(), project=project_name, source=source, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def scan_project_files(project: Dict[str, Any], extensions: Set[str], scan_date: str) -> List[AirportPriceFile]:
    project_name = str(project.get("name") or "airport_project")
    found: Dict[str, AirportPriceFile] = {}

    crawler_config_path = Path(str(project.get("crawler_config") or ""))
    if crawler_config_path.exists():
        try:
            payload = json.loads(crawler_config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            payload = {}
        for raw_path in payload.get("generated_files") or []:
            path = Path(str(raw_path))
            if path.suffix.lower() not in extensions:
                continue
            if "price_data" not in [part.lower() for part in path.parts]:
                continue
            item = _file_from_path(path, project_name, "crawler_config")
            if item:
                found[item.key] = item

    price_data_root = Path(str(project.get("price_data_root") or ""))
    date_dir = price_data_root / scan_date
    if date_dir.exists():
        for path in date_dir.rglob("*"):
            if path.suffix.lower() not in extensions:
                continue
            item = _file_from_path(path, project_name, "price_data_scan")
            if item:
                found[item.key] = item

    return sorted(found.values(), key=lambda item: (item.path.stat().st_mtime, str(item.path)))


def scan_files(config: Dict[str, Any], scan_date: str) -> List[AirportPriceFile]:
    items: List[AirportPriceFile] = []
    for project in config.get("projects") or []:
        items.extend(scan_project_files(project, config["extensions"], scan_date))
    return items


def find_chat_id(client: FeishuClient, chat_name: str, state: AirportGroupSyncState) -> str:
    cached = state.data.get("chat") or {}
    if cached.get("name") == chat_name and cached.get("chat_id"):
        return str(cached["chat_id"])

    chats = client.paginate("GET", "/im/v1/chats", params={"user_id_type": "open_id"}, item_key="items", page_size=100)
    matches = [chat for chat in chats if str(chat.get("name") or "").strip() == chat_name]
    if not matches:
        raise FeishuError(f"没有找到群聊: {chat_name}。请确认机器人已加入该群。")
    chat_id = str(matches[0].get("chat_id") or "").strip()
    if not chat_id:
        raise FeishuError(f"群聊 {chat_name} 没有返回 chat_id")
    state.data["chat"] = {"name": chat_name, "chat_id": chat_id, "resolved_at": datetime.now().isoformat(timespec="seconds")}
    state.save()
    return chat_id


def upload_im_file(client: FeishuClient, item: AirportPriceFile) -> str:
    with item.path.open("rb") as file:
        data = {
            "file_type": "stream",
            "file_name": item.path.name,
        }
        result = client.request(
            "POST",
            "/im/v1/files",
            data=data,
            files={"file": (item.path.name, file)},
            timeout=300,
        )
    file_key = str(result.get("file_key") or "").strip()
    if not file_key:
        raise FeishuError(f"上传文件后未返回 file_key: {item.path}")
    return file_key


def send_file_message(client: FeishuClient, chat_id: str, file_key: str) -> str:
    result = client.request(
        "POST",
        "/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        body={
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        },
        timeout=120,
    )
    return str(result.get("message_id") or "").strip()


def send_text_message(client: FeishuClient, chat_id: str, text: str) -> str:
    result = client.request(
        "POST",
        "/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        body={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        timeout=120,
    )
    return str(result.get("message_id") or "").strip()


def notify_sync_error(subject: str, detail: str) -> None:
    if feishu_notifier is None:
        return
    try:
        sent = feishu_notifier.发送告警(
            "\n".join(
                [
                    f"price-data 文件同步异常：{subject}",
                    f"发生时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
                    f"详情：{str(detail or '未知错误').strip()[:1200]}",
                ]
            )
        )
        if not sent:
            print("未配置自动化程序告警群，已跳过飞书告警")
    except Exception as notify_exc:
        print(f"飞书告警发送失败：{notify_exc}")


def sync_once(config: Dict[str, Any], *, scan_date: str, dry_run: bool = False) -> int:
    state = AirportGroupSyncState(Path(config["state_file"]))
    state.load()
    items = [item for item in scan_files(config, scan_date) if not state.already_synced(item)]
    if not items:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 没有待上传的机场 price_data 文件")
        return 0

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 待上传文件 {len(items)} 个")
    if dry_run:
        for item in items:
            print(f"[DRY-RUN] {item.project}: {item.path}")
        return 0

    client = FeishuClient(config["app_id"], config["app_secret"])
    chat_id = find_chat_id(client, str(config.get("chat_name") or "price-data-sync"), state)
    sent = 0
    for item in items:
        print(f"上传到飞书群 {config['chat_name']}: {item.path}")
        file_hash = sha256_file(item.path)
        file_key = upload_im_file(client, item)
        message_id = send_file_message(client, chat_id, file_key)
        state.mark_sent(item, {"file_key": file_key, "message_id": message_id, "sha256": file_hash})
        state.save()
        sent += 1
    return sent


def run_watch(config: Dict[str, Any], *, scan_date: str) -> None:
    print(f"机场 price_data 飞书群同步 watch 已启动，群: {config.get('chat_name')}, 日期: {scan_date}")
    while True:
        try:
            sync_once(config, scan_date=scan_date)
        except Exception as exc:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 同步失败: {exc}")
            notify_sync_error("监听轮次失败", str(exc))
        time.sleep(max(10, int(config.get("poll_interval_seconds") or 60)))


def main() -> int:
    parser = argparse.ArgumentParser(description="同步机场接送 price_data 文件到飞书群")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--date", default=DEFAULT_SCAN_DATE, help="扫描日期 YYYYMMDD，默认今天")
    parser.add_argument("--watch", action="store_true", help="循环监控新增文件")
    parser.add_argument("--dry-run", action="store_true", help="只打印待上传文件，不发送")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if args.watch:
        run_watch(config, scan_date=args.date)
        return 0
    sent = sync_once(config, scan_date=args.date, dry_run=args.dry_run)
    print(f"本轮上传完成: {sent} 个文件")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        notify_sync_error("进程退出", str(exc))
        print(f"同步失败：{exc}", file=sys.stderr)
        raise SystemExit(1)

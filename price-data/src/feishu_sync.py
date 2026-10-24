import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime with a clearer message.
    requests = None

try:
    import feishu_notifier
except ImportError:  # pragma: no cover - notification is best effort.
    feishu_notifier = None


BASE_URL = "https://open.feishu.cn/open-apis"
CHUNK_SIZE = 4 * 1024 * 1024
MAX_DIRECT_UPLOAD_SIZE = 20 * 1024 * 1024
DATE_HEADING_LEVEL = 2
DATE_HEADING_BLOCK_TYPE = 2 + DATE_HEADING_LEVEL
HEADING_BLOCK_TYPES = set(range(3, 12))
NETWORK_RETRY_ATTEMPTS = 4
NETWORK_RETRY_DELAYS_SECONDS = (1, 2, 4)
NETWORK_RETRY_JITTER_MAX_SECONDS = 0.3

FILENAME_RE = re.compile(
    r"^(?P<code>[A-Z0-9]+)_(?P<kind>filtered_data|min|BM|IX)_?"
    r"(?P<date>\d{8})_(?P<time>\d{6})\.(?P<ext>csv|xlsx)$",
    re.IGNORECASE,
)


class FeishuError(RuntimeError):
    pass


def notify_sync_error(subject: str, detail: str) -> None:
    if feishu_notifier is None:
        return
    try:
        sent = feishu_notifier.发送告警(
            "\n".join(
                [
                    f"price-data Wiki 同步异常：{subject}",
                    f"发生时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
                    f"详情：{str(detail or '未知错误').strip()[:1200]}",
                ]
            )
        )
        if not sent:
            print("未配置自动化程序告警群，已跳过飞书告警")
    except Exception as notify_exc:
        print(f"飞书告警发送失败：{notify_exc}")


@dataclass
class PriceFile:
    path: Path
    code: str
    kind: str
    date: str
    time_text: str
    ext: str
    size: int
    mtime_ns: int
    root: Path

    @property
    def key(self) -> str:
        return str(self.path.resolve()).lower()

    @property
    def signature(self) -> str:
        return "%s:%s" % (self.size, self.mtime_ns)

    @property
    def display_date(self) -> str:
        return datetime.strptime(self.date, "%Y%m%d").strftime("%Y-%m-%d")


class SyncState:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.data = {"version": 1, "files": {}}

    def load(self) -> None:
        if not self.state_path.exists():
            return
        with self.state_path.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.data.setdefault("version", 1)
        self.data.setdefault("files", {})

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path.replace(self.state_path)

    def get(self, item: PriceFile) -> Optional[Dict[str, Any]]:
        return self.data["files"].get(item.key)

    def put(self, item: PriceFile, values: Dict[str, Any]) -> None:
        record = dict(values)
        record.update(
            {
                "path": str(item.path.resolve()),
                "code": item.code,
                "kind": item.kind,
                "date": item.date,
                "time": item.time_text,
                "ext": item.ext,
                "size": item.size,
                "mtime_ns": item.mtime_ns,
                "signature": item.signature,
                "synced_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.data["files"][item.key] = record


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, min_interval_seconds: float = 0.35) -> None:
        if requests is None:
            raise FeishuError("缺少 requests 依赖，请先运行：python -m pip install requests")
        if not app_id or not app_secret:
            raise FeishuError("缺少飞书应用凭证，请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        self.app_id = app_id
        self.app_secret = app_secret
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._last_request_at = 0.0
        self.token = self._get_tenant_access_token()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def _retry_delay_seconds(self, attempt_index: int) -> float:
        if attempt_index <= 0:
            return 0.0
        delay_index = min(attempt_index - 1, len(NETWORK_RETRY_DELAYS_SECONDS) - 1)
        return NETWORK_RETRY_DELAYS_SECONDS[delay_index] + random.uniform(0.0, NETWORK_RETRY_JITTER_MAX_SECONDS)

    def _is_retryable_request_exception(self, exc: Exception) -> bool:
        request_exceptions = getattr(requests, "exceptions", None)
        if request_exceptions is None:
            return False
        retryable_types = tuple(
            exc_type
            for exc_type in (
                getattr(request_exceptions, "ConnectionError", None),
                getattr(request_exceptions, "Timeout", None),
                getattr(request_exceptions, "ChunkedEncodingError", None),
            )
            if exc_type is not None
        )
        return bool(retryable_types) and isinstance(exc, retryable_types)

    def _capture_file_positions(self, files: Optional[Dict[str, Any]]) -> Dict[str, int]:
        if not isinstance(files, dict):
            return {}
        positions: Dict[str, int] = {}
        for key, value in files.items():
            candidate = value[1] if isinstance(value, tuple) and len(value) >= 2 else value
            if not hasattr(candidate, "tell") or not hasattr(candidate, "seek"):
                continue
            try:
                positions[key] = int(candidate.tell())
            except Exception:
                continue
        return positions

    def _rewind_files(self, files: Optional[Dict[str, Any]], positions: Dict[str, int]) -> None:
        if not isinstance(files, dict) or not positions:
            return
        for key, position in positions.items():
            value = files.get(key)
            candidate = value[1] if isinstance(value, tuple) and len(value) >= 2 else value
            if not hasattr(candidate, "seek"):
                continue
            try:
                candidate.seek(position)
            except Exception:
                continue

    def _execute_json_request(self, sender, *, files: Optional[Dict[str, Any]] = None) -> Tuple[Any, Dict[str, Any]]:
        file_positions = self._capture_file_positions(files)
        for attempt_index in range(NETWORK_RETRY_ATTEMPTS):
            if attempt_index:
                self._rewind_files(files, file_positions)
            self._throttle()
            try:
                response = sender()
            except Exception as exc:
                if attempt_index >= NETWORK_RETRY_ATTEMPTS - 1 or not self._is_retryable_request_exception(exc):
                    raise
                time.sleep(self._retry_delay_seconds(attempt_index + 1))
                continue

            self._last_request_at = time.monotonic()
            if (
                response.status_code == 429 or 500 <= response.status_code < 600
            ) and attempt_index < NETWORK_RETRY_ATTEMPTS - 1:
                time.sleep(self._retry_delay_seconds(attempt_index + 1))
                continue
            return response, _json_response(response)

        raise FeishuError("Feishu request exhausted retries without a final response")

    def _get_tenant_access_token(self) -> str:
        _, payload = self._execute_json_request(
            lambda: requests.post(
                BASE_URL + "/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=30,
            )
        )
        if payload.get("code") != 0:
            raise FeishuError("获取 tenant_access_token 失败：%s" % payload)
        return payload["tenant_access_token"]

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        timeout: int = 120,
        retry: bool = True,
    ) -> Dict[str, Any]:
        url = BASE_URL + "/" + path.lstrip("/")
        headers = {"Authorization": "Bearer %s" % self.token}
        if body is not None and files is None:
            headers["Content-Type"] = "application/json; charset=utf-8"

        response, payload = self._execute_json_request(
            lambda: requests.request(
                method,
                url,
                headers=headers,
                params=params or {},
                json=body if files is None else None,
                data=data if files is not None else None,
                files=files,
                timeout=timeout,
            ),
            files=files,
        )
        if _token_expired(payload) and retry:
            self.token = self._get_tenant_access_token()
            return self.request(method, path, params, body, data, files, timeout, retry=False)
        if payload.get("code") != 0:
            raise FeishuError(
                "飞书 API 请求失败：method=%s path=%s status=%s response=%s"
                % (method, path, response.status_code, payload)
            )
        return payload.get("data") or {}

    def paginate(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        item_key: str = "items",
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        page_token = None
        while True:
            request_params = dict(params or {})
            request_params["page_size"] = page_size
            if page_token:
                request_params["page_token"] = page_token
            data = self.request(method, path, params=request_params)
            result.extend(data.get(item_key) or [])
            if not data.get("has_more") or not data.get("page_token"):
                break
            page_token = data["page_token"]
        return result

    def get_wiki_node(self, wiki_token: str) -> Dict[str, Any]:
        data = self.request("GET", "/wiki/v2/spaces/get_node", params={"token": wiki_token})
        return data["node"]

    def list_wiki_children(self, space_id: str, parent_node_token: str) -> List[Dict[str, Any]]:
        return self.paginate(
            "GET",
            "/wiki/v2/spaces/%s/nodes" % space_id,
            params={"parent_node_token": parent_node_token},
            page_size=50,
        )

    def create_wiki_docx(self, space_id: str, parent_node_token: str, title: str) -> Dict[str, Any]:
        data = self.request(
            "POST",
            "/wiki/v2/spaces/%s/nodes" % space_id,
            body={
                "obj_type": "docx",
                "node_type": "origin",
                "parent_node_token": parent_node_token,
                "title": title,
            },
        )
        return data["node"]

    def get_root_children(self, document_id: str) -> List[Dict[str, Any]]:
        return self.paginate(
            "GET",
            "/docx/v1/documents/%s/blocks/%s/children" % (document_id, document_id),
            params={"document_revision_id": -1},
            page_size=500,
        )

    def convert_markdown(self, markdown: str) -> List[Dict[str, Any]]:
        data = self.request(
            "POST",
            "/docx/v1/documents/blocks/convert",
            body={"content": markdown, "content_type": "markdown"},
        )
        block_by_id = {block["block_id"]: block for block in data.get("blocks") or []}
        children = []
        for block_id in data.get("first_level_block_ids") or []:
            block = dict(block_by_id.get(block_id) or {})
            for key in ("block_id", "parent_id", "children"):
                block.pop(key, None)
            if block:
                children.append(block)
        return children

    def create_blocks(
        self,
        document_id: str,
        parent_block_id: str,
        children: List[Dict[str, Any]],
        index: int,
        client_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"document_revision_id": -1}
        if client_token:
            params["client_token"] = client_token
        return self.request(
            "POST",
            "/docx/v1/documents/%s/blocks/%s/children" % (document_id, parent_block_id),
            params=params,
            body={"children": children, "index": index},
        )

    def update_block(self, document_id: str, block_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request(
            "PATCH",
            "/docx/v1/documents/%s/blocks/%s" % (document_id, block_id),
            params={"document_revision_id": -1},
            body=payload,
        )

    def delete_child_block(
        self,
        document_id: str,
        parent_block_id: str,
        start_index: int,
        end_index: int,
        client_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"document_revision_id": -1}
        if client_token:
            params["client_token"] = client_token
        return self.request(
            "DELETE",
            "/docx/v1/documents/%s/blocks/%s/children/batch_delete" % (document_id, parent_block_id),
            params=params,
            body={"start_index": start_index, "end_index": end_index},
        )

    def upload_docx_media(self, file_path: Path, parent_node: str, extra: Optional[str] = None) -> str:
        size = file_path.stat().st_size
        if size <= MAX_DIRECT_UPLOAD_SIZE:
            return self._upload_all(file_path, parent_node, size, extra=extra)
        return self._upload_multipart(file_path, parent_node, size, extra=extra)

    def _upload_all(self, file_path: Path, parent_node: str, size: int, extra: Optional[str] = None) -> str:
        data = {
            "file_name": file_path.name,
            "parent_type": "docx_file",
            "parent_node": parent_node,
            "size": str(size),
        }
        if extra:
            data["extra"] = extra
        with file_path.open("rb") as f:
            result = self.request(
                "POST",
                "/drive/v1/medias/upload_all",
                data=data,
                files={"file": (file_path.name, f)},
                timeout=300,
            )
        return result["file_token"]

    def _upload_multipart(self, file_path: Path, parent_node: str, size: int, extra: Optional[str] = None) -> str:
        body: Dict[str, Any] = {
            "file_name": file_path.name,
            "parent_type": "docx_file",
            "parent_node": parent_node,
            "size": size,
        }
        if extra:
            body["extra"] = extra
        prepare = self.request(
            "POST",
            "/drive/v1/medias/upload_prepare",
            body=body,
            timeout=300,
        )
        upload_id = prepare["upload_id"]
        block_num = prepare["block_num"]
        with file_path.open("rb") as f:
            for seq in range(block_num):
                chunk = f.read(CHUNK_SIZE)
                self.request(
                    "POST",
                    "/drive/v1/medias/upload_part",
                    data={"upload_id": upload_id, "seq": str(seq), "size": str(len(chunk))},
                    files={"file": ("part_%s" % seq, chunk)},
                    timeout=300,
                )
        finish = self.request(
            "POST",
            "/drive/v1/medias/upload_finish",
            body={"upload_id": upload_id, "block_num": block_num},
            timeout=300,
        )
        return finish["file_token"]


def _json_response(response: Any) -> Dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise FeishuError("飞书返回了非 JSON 响应：status=%s text=%s" % (response.status_code, response.text)) from exc
    if response.status_code >= 400:
        raise FeishuError("飞书 HTTP 请求失败：status=%s response=%s" % (response.status_code, payload))
    return payload


def _token_expired(payload: Dict[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return payload.get("code") == 99991663 or "invalid access token" in text


def load_config(config_path: Optional[str]) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    default_config = Path(__file__).with_name("feishu_sync_config.json")
    example_config = Path(__file__).with_name("feishu_sync_config.example.json")
    path = Path(config_path) if config_path else default_config
    if not path.exists() and not config_path:
        path = example_config
    if not path.exists():
        raise FileNotFoundError("找不到配置文件：%s" % path)

    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    config["_config_path"] = str(path)
    config["_project_root"] = str(project_root)
    config["app_id"] = resolve_env_value(config.get("app_id")) or os.getenv("FEISHU_APP_ID", "")
    config["app_secret"] = resolve_env_value(config.get("app_secret")) or os.getenv("FEISHU_APP_SECRET", "")
    config["state_file"] = str(resolve_path(config.get("state_file", "src/feishu_sync_state.json"), project_root))
    config.setdefault("extensions", [".csv", ".xlsx"])
    config.setdefault("include_codes", [])
    config.setdefault("exclude_codes", [])
    config.setdefault("code_documents", {})
    config.setdefault("code_document_titles", {})
    config.setdefault("create_missing_documents", True)
    config.setdefault("settle_seconds", 5)
    config.setdefault("api_min_interval_seconds", 0.35)
    config.setdefault("date_heading_format", "%Y-%m-%d")
    return config


def resolve_env_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)
    if match:
        return os.getenv(match.group(1), "")
    return os.path.expandvars(text)


def resolve_path(value: str, project_root: Path) -> Path:
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = project_root / path
    return path


def scan_price_files(config: Dict[str, Any]) -> List[PriceFile]:
    extensions = {ext.lower() if ext.startswith(".") else "." + ext.lower() for ext in config["extensions"]}
    include_codes = {code.upper() for code in config.get("include_codes") or []}
    exclude_codes = {code.upper() for code in config.get("exclude_codes") or []}
    settle_seconds = float(config.get("settle_seconds", 0))
    now = time.time()
    items: List[PriceFile] = []

    for root_text in config.get("data_roots") or []:
        root = Path(os.path.expandvars(root_text))
        if not root.exists():
            print("跳过不存在的目录：%s" % root)
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            match = FILENAME_RE.match(path.name)
            if not match:
                continue
            code = match.group("code").upper()
            kind = match.group("kind")
            date = match.group("date")
            time_text = match.group("time")
            ext = match.group("ext").lower()
            if include_codes and code not in include_codes:
                continue
            if code in exclude_codes:
                continue
            stat = path.stat()
            if settle_seconds and now - stat.st_mtime < settle_seconds:
                continue
            items.append(
                PriceFile(
                    path=path,
                    code=code,
                    kind=kind,
                    date=date,
                    time_text=time_text,
                    ext=ext,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    root=root,
                )
            )
    return sorted(items, key=lambda item: (item.code, item.date, item.kind.lower(), item.path.name.lower()))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pending_files(items: List[PriceFile], state: SyncState, resync_all: bool = False) -> List[Tuple[PriceFile, str]]:
    pending: List[Tuple[PriceFile, str]] = []
    for item in items:
        old = state.get(item)
        if old is None or resync_all:
            pending.append((item, "new"))
        elif old.get("signature") != item.signature:
            current_hash = sha256_file(item.path)
            if current_hash != old.get("sha256"):
                pending.append((item, "updated"))
    return pending


class DocumentResolver:
    def __init__(self, client: FeishuClient, config: Dict[str, Any]) -> None:
        self.client = client
        self.config = config
        self.root_node = self.client.get_wiki_node(parse_wiki_token(config["wiki_url"]))
        self.space_id = self.root_node["space_id"]
        self.root_node_token = self.root_node["node_token"]
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.child_cache: Optional[Dict[str, Dict[str, Any]]] = None

    def get_document_for_code(self, code: str) -> Dict[str, Any]:
        code = code.upper()
        if code in self.cache:
            return self.cache[code]

        explicit = (self.config.get("code_documents") or {}).get(code)
        if explicit:
            node = self.resolve_document_ref(explicit)
            self.cache[code] = node
            return node

        children = self._child_nodes_by_title()
        title = (self.config.get("code_document_titles") or {}).get(code, code)
        node = children.get(normalize_title(title))
        if node is None and self.config.get("create_missing_documents", True):
            node = self.client.create_wiki_docx(self.space_id, self.root_node_token, title)
        if node is None:
            raise FeishuError("找不到代码 %s 对应的飞书文档，请创建同名 Wiki 子文档或配置 code_documents" % code)
        if node.get("obj_type") not in ("docx", "doc"):
            raise FeishuError("代码 %s 对应节点类型是 %s，当前脚本只支持新版文档 docx" % (code, node.get("obj_type")))
        self.cache[code] = node
        return node

    def resolve_document_ref(self, value: str) -> Dict[str, Any]:
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            parts = [part for part in parsed.path.split("/") if part]
            if "wiki" in parts:
                return self.client.get_wiki_node(parts[parts.index("wiki") + 1])
            if "docx" in parts:
                token = parts[parts.index("docx") + 1]
                return {"obj_token": token, "obj_type": "docx", "node_token": "", "title": token}
        try:
            return self.client.get_wiki_node(value)
        except FeishuError:
            return {"obj_token": value, "obj_type": "docx", "node_token": "", "title": value}

    def _child_nodes_by_title(self) -> Dict[str, Dict[str, Any]]:
        if self.child_cache is None:
            children = self.client.list_wiki_children(self.space_id, self.root_node_token)
            self.child_cache = {normalize_title(item.get("title", "")): item for item in children if item.get("title")}
        return self.child_cache


def parse_wiki_token(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if "wiki" not in parts:
        raise ValueError("wiki_url 必须是飞书 Wiki 链接：%s" % url)
    index = parts.index("wiki") + 1
    if index >= len(parts):
        raise ValueError("wiki_url 中没有找到 Wiki token：%s" % url)
    return parts[index]


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def normalize_date(value: str) -> str:
    return re.sub(r"\D", "", value)


def extract_block_text(block: Dict[str, Any]) -> str:
    for value in block.values():
        if isinstance(value, dict) and "elements" in value:
            pieces = []
            for element in value.get("elements") or []:
                text_run = element.get("text_run") or {}
                if "content" in text_run:
                    pieces.append(text_run["content"])
            if pieces:
                return "".join(pieces)
    return ""


def find_date_insert_index(children: List[Dict[str, Any]], date_text: str) -> Tuple[bool, int]:
    target = normalize_date(date_text)
    heading_index = None
    for index, block in enumerate(children):
        if block.get("block_type") not in HEADING_BLOCK_TYPES:
            continue
        current = normalize_date(extract_block_text(block))
        if current == target:
            heading_index = index
            break
        if len(current) == 8 and current < target:
            return False, index

    if heading_index is None:
        return False, len(children)

    insert_index = heading_index + 1
    while insert_index < len(children):
        block_type = children[insert_index].get("block_type")
        if block_type == DATE_HEADING_BLOCK_TYPE:
            break
        insert_index += 1
    return True, insert_index


def ensure_date_section(client: FeishuClient, document_id: str, date_text: str) -> int:
    children = client.get_root_children(document_id)
    exists, index = find_date_insert_index(children, date_text)
    if exists:
        return index
    heading_children = client.convert_markdown("## %s" % date_text)
    client.create_blocks(document_id, document_id, heading_children, index)
    return index + len(heading_children)


def insert_file_block(client: FeishuClient, document_id: str, insert_index: int, item: PriceFile) -> Dict[str, str]:
    client_token = hashlib.md5((item.key + ":" + item.signature).encode("utf-8")).hexdigest()
    result = client.create_blocks(
        document_id,
        document_id,
        [{"block_type": 23, "file": {"token": ""}}],
        insert_index,
        client_token=client_token,
    )
    created = (result.get("children") or [{}])[0]
    view_block_id = created.get("block_id", "")
    file_block_id = view_block_id
    if created.get("children"):
        file_block_id = created["children"][0]
    if file_block_id == view_block_id:
        refreshed_children = client.get_root_children(document_id)
        for block in refreshed_children:
            if block.get("block_id") == view_block_id and block.get("children"):
                file_block_id = block["children"][0]
                break
    try:
        file_token = client.upload_docx_media(item.path, file_block_id)
    except FeishuError as exc:
        if "1770002" not in str(exc):
            raise
        file_token = client.upload_docx_media(item.path, document_id)
    client.update_block(document_id, file_block_id, {"replace_file": {"token": file_token}})
    return {"view_block_id": view_block_id, "file_block_id": file_block_id, "file_token": file_token}


def update_file_block(client: FeishuClient, document_id: str, item: PriceFile, old: Dict[str, Any]) -> Dict[str, str]:
    file_block_id = old.get("file_block_id")
    if not file_block_id:
        raise FeishuError("本地状态缺少 file_block_id，无法原位更新：%s" % item.path)
    try:
        file_token = client.upload_docx_media(item.path, file_block_id)
    except FeishuError as exc:
        if "1770002" not in str(exc):
            raise
        file_token = client.upload_docx_media(item.path, document_id)
    client.update_block(document_id, file_block_id, {"replace_file": {"token": file_token}})
    return {
        "view_block_id": old.get("view_block_id", ""),
        "file_block_id": file_block_id,
        "file_token": file_token,
    }


def sync_once(config: Dict[str, Any], args: argparse.Namespace) -> int:
    state = SyncState(Path(config["state_file"]))
    state.load()
    items = scan_price_files(config)
    pending = pending_files(items, state, resync_all=args.resync_all)
    if args.limit:
        pending = pending[: args.limit]

    print("扫描到 %s 个价格文件，待同步 %s 个。" % (len(items), len(pending)))
    if args.dry_run:
        print_pending_summary(pending)
        return 0
    if not pending:
        return 0

    client = FeishuClient(
        config["app_id"],
        config["app_secret"],
        min_interval_seconds=float(config.get("api_min_interval_seconds", 0.35)),
    )
    resolver = DocumentResolver(client, config)

    synced = 0
    for item, reason in pending:
        old = state.get(item)
        file_hash = sha256_file(item.path)
        node = resolver.get_document_for_code(item.code)
        document_id = node["obj_token"]
        date_text = datetime.strptime(item.date, "%Y%m%d").strftime(config.get("date_heading_format", "%Y-%m-%d"))

        print("[%s] %s -> %s / %s" % (reason, item.path.name, item.code, date_text))
        try:
            if reason == "updated" and old and old.get("document_id") == document_id and old.get("file_block_id"):
                block_info = update_file_block(client, document_id, item, old)
            else:
                insert_index = ensure_date_section(client, document_id, date_text)
                block_info = insert_file_block(client, document_id, insert_index, item)
        except FeishuError:
            state.save()
            raise

        state.put(
            item,
            {
                "sha256": file_hash,
                "document_id": document_id,
                "node_token": node.get("node_token", ""),
                "obj_type": node.get("obj_type", ""),
                "document_title": node.get("title", item.code),
                **block_info,
            },
        )
        state.save()
        synced += 1

    print("同步完成：%s 个文件。" % synced)
    return 0


def cleanup_synced_blocks(config: Dict[str, Any], args: argparse.Namespace) -> int:
    state = SyncState(Path(config["state_file"]))
    state.load()
    records = state.data.get("files", {})
    if not records:
        print("没有需要清理的同步记录。")
        return 0

    client = FeishuClient(
        config["app_id"],
        config["app_secret"],
        min_interval_seconds=float(config.get("api_min_interval_seconds", 0.35)),
    )
    grouped: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for key, record in records.items():
        document_id = record.get("document_id")
        view_block_id = record.get("view_block_id")
        if document_id and view_block_id:
            grouped.setdefault(document_id, []).append((key, record))

    removed = 0
    missing = 0
    for document_id, document_records in grouped.items():
        children = client.get_root_children(document_id)
        record_block_ids = {
            str(record.get("view_block_id"))
            for _key, record in document_records
            if record.get("view_block_id")
        }
        record_dates = {
            normalize_date(str(record.get("date", "")))
            for _key, record in document_records
            if record.get("date")
        }
        section_ranges: List[Tuple[int, int]] = []
        protected_record_block_ids = set()
        for index, block in enumerate(children):
            if block.get("block_type") != DATE_HEADING_BLOCK_TYPE:
                continue
            date_key = normalize_date(extract_block_text(block))
            if date_key not in record_dates:
                continue
            end = index + 1
            while end < len(children):
                next_block = children[end]
                if (
                    next_block.get("block_type") == DATE_HEADING_BLOCK_TYPE
                    and len(normalize_date(extract_block_text(next_block))) == 8
                ):
                    break
                end += 1
            body_ids = {
                child.get("block_id")
                for child in children[index + 1 : end]
                if child.get("block_id")
            }
            if body_ids and body_ids.issubset(record_block_ids):
                section_ranges.append((index, end))
                protected_record_block_ids.update(body_ids)

        for start, end in sorted(section_ranges, reverse=True):
            heading_text = extract_block_text(children[start])
            print("删除旧日期段：%s / %s，块范围 %s-%s" % (document_id, heading_text, start, end))
            if not args.dry_run:
                token = hashlib.md5((document_id + ":section:" + str(start) + ":" + str(end)).encode("utf-8")).hexdigest()
                client.delete_child_block(document_id, document_id, start, end, client_token=token)
                for key, record in list(document_records):
                    if record.get("view_block_id") in protected_record_block_ids:
                        state.data["files"].pop(key, None)
                state.save()
            removed += end - start

        if section_ranges and not args.dry_run:
            children = client.get_root_children(document_id)

        index_by_block_id = {
            block.get("block_id"): index
            for index, block in enumerate(children)
            if block.get("block_id")
        }
        for key, record in sorted(document_records, key=lambda pair: index_by_block_id.get(pair[1].get("view_block_id"), -1), reverse=True):
            view_block_id = record.get("view_block_id")
            if view_block_id in protected_record_block_ids:
                continue
            index = index_by_block_id.get(view_block_id)
            if index is None:
                missing += 1
                state.data["files"].pop(key, None)
                continue
            print("删除已同步附件块：%s / %s" % (record.get("document_title", document_id), record.get("path", view_block_id)))
            if not args.dry_run:
                token = hashlib.md5((document_id + ":" + str(view_block_id)).encode("utf-8")).hexdigest()
                client.delete_child_block(document_id, document_id, index, index + 1, client_token=token)
                state.data["files"].pop(key, None)
                state.save()
            removed += 1

    if args.dry_run:
        print("dry-run：将删除 %s 个附件块，%s 条记录对应块已不存在。" % (removed, missing))
    else:
        state.save()
        print("清理完成：删除 %s 个附件块，移除 %s 条失效记录。" % (removed, missing))
    return 0


def cleanup_empty_date_headings(config: Dict[str, Any], args: argparse.Namespace) -> int:
    client = FeishuClient(
        config["app_id"],
        config["app_secret"],
        min_interval_seconds=float(config.get("api_min_interval_seconds", 0.35)),
    )
    resolver = DocumentResolver(client, config)
    codes = sorted({item.code for item in scan_price_files(config)})
    removed = 0

    for code in codes:
        try:
            node = resolver.get_document_for_code(code)
        except FeishuError as exc:
            print("跳过 %s：%s" % (code, exc))
            continue
        document_id = node["obj_token"]
        children = client.get_root_children(document_id)
        for index in range(len(children) - 1, -1, -1):
            block = children[index]
            if block.get("block_type") != DATE_HEADING_BLOCK_TYPE:
                continue
            date_key = normalize_date(extract_block_text(block))
            if len(date_key) != 8:
                continue
            next_is_date_heading = (
                index + 1 >= len(children)
                or (
                    children[index + 1].get("block_type") == DATE_HEADING_BLOCK_TYPE
                    and len(normalize_date(extract_block_text(children[index + 1]))) == 8
                )
            )
            if not next_is_date_heading:
                continue
            print("删除空日期标题：%s / %s" % (code, extract_block_text(block)))
            if not args.dry_run:
                token = hashlib.md5((document_id + ":empty:" + str(block.get("block_id"))).encode("utf-8")).hexdigest()
                client.delete_child_block(document_id, document_id, index, index + 1, client_token=token)
            removed += 1

    if args.dry_run:
        print("dry-run：将删除 %s 个空日期标题。" % removed)
    else:
        print("空日期标题清理完成：%s 个。" % removed)
    return 0


def cleanup_untracked_date_sections(config: Dict[str, Any], args: argparse.Namespace) -> int:
    state = SyncState(Path(config["state_file"]))
    state.load()
    tracked_blocks = {
        str(record.get("view_block_id"))
        for record in state.data.get("files", {}).values()
        if record.get("view_block_id")
    }
    local_dates_by_code: Dict[str, set] = {}
    for item in scan_price_files(config):
        local_dates_by_code.setdefault(item.code, set()).add(item.date)

    client = FeishuClient(
        config["app_id"],
        config["app_secret"],
        min_interval_seconds=float(config.get("api_min_interval_seconds", 0.35)),
    )
    resolver = DocumentResolver(client, config)
    removed = 0

    for code, local_dates in sorted(local_dates_by_code.items()):
        try:
            node = resolver.get_document_for_code(code)
        except FeishuError as exc:
            print("跳过 %s：%s" % (code, exc))
            continue
        document_id = node["obj_token"]
        children = client.get_root_children(document_id)
        ranges: List[Tuple[int, int, str]] = []
        for index, block in enumerate(children):
            if block.get("block_type") != DATE_HEADING_BLOCK_TYPE:
                continue
            date_key = normalize_date(extract_block_text(block))
            if date_key not in local_dates:
                continue
            end = index + 1
            while end < len(children):
                next_block = children[end]
                if (
                    next_block.get("block_type") == DATE_HEADING_BLOCK_TYPE
                    and len(normalize_date(extract_block_text(next_block))) == 8
                ):
                    break
                end += 1
            body_ids = {
                child.get("block_id")
                for child in children[index + 1 : end]
                if child.get("block_id")
            }
            if tracked_blocks.intersection(body_ids):
                continue
            ranges.append((index, end, extract_block_text(block)))

        for start, end, heading in sorted(ranges, reverse=True):
            print("删除未跟踪日期段：%s / %s，块范围 %s-%s" % (code, heading, start, end))
            if not args.dry_run:
                token = hashlib.md5((document_id + ":untracked:" + str(start) + ":" + str(end)).encode("utf-8")).hexdigest()
                client.delete_child_block(document_id, document_id, start, end, client_token=token)
            removed += end - start

    if args.dry_run:
        print("dry-run：将删除 %s 个未跟踪日期段块。" % removed)
    else:
        print("未跟踪日期段清理完成：%s 个块。" % removed)
    return 0


def print_pending_summary(pending: List[Tuple[PriceFile, str]]) -> None:
    groups: Dict[Tuple[str, str], int] = {}
    for item, _reason in pending:
        groups[(item.code, item.date)] = groups.get((item.code, item.date), 0) + 1
    for (code, date), count in sorted(groups.items()):
        print("  %s / %s：%s 个文件" % (code, date, count))
    for item, reason in pending[:20]:
        print("  - [%s] %s" % (reason, item.path))
    if len(pending) > 20:
        print("  ... 还有 %s 个文件" % (len(pending) - 20))


def run_watch(config: Dict[str, Any], args: argparse.Namespace) -> int:
    interval = max(10, int(args.interval))
    print("进入监听模式，每 %s 秒扫描一次。按 Ctrl+C 停止。" % interval)
    while True:
        try:
            sync_once(config, args)
        except Exception as exc:
            print(
                "[%s] 本轮同步失败，下一轮会继续重试：%s"
                % (datetime.now().isoformat(timespec="seconds"), exc),
                file=sys.stderr,
            )
            notify_sync_error("监听轮次失败", str(exc))
        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同步本地 price_data 文件到飞书 Wiki 文档")
    parser.add_argument("--config", help="配置文件路径，默认使用 src/feishu_sync_config.json 或 example")
    parser.add_argument("--dry-run", action="store_true", help="只扫描并打印待同步文件，不调用飞书 API")
    parser.add_argument("--resync-all", action="store_true", help="忽略本地状态，重新同步所有匹配文件")
    parser.add_argument("--limit", type=int, default=0, help="最多同步多少个待处理文件，0 表示不限制")
    parser.add_argument("--watch", action="store_true", help="持续监听，定时扫描本地更新")
    parser.add_argument("--interval", type=int, default=300, help="监听模式扫描间隔秒数，默认 300")
    parser.add_argument("--cleanup-synced-blocks", action="store_true", help="删除状态文件中记录过的飞书附件块，并清空这些同步记录")
    parser.add_argument("--cleanup-empty-date-headings", action="store_true", help="删除空的日期标题块")
    parser.add_argument("--cleanup-untracked-date-sections", action="store_true", help="删除本地日期对应但未在状态中记录的日期段")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.cleanup_synced_blocks:
        return cleanup_synced_blocks(config, args)
    if args.cleanup_empty_date_headings:
        return cleanup_empty_date_headings(config, args)
    if args.cleanup_untracked_date_sections:
        return cleanup_untracked_date_sections(config, args)
    if args.watch:
        return run_watch(config, args)
    return sync_once(config, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
        raise SystemExit(130)
    except Exception as exc:
        print("同步失败：%s" % exc, file=sys.stderr)
        notify_sync_error("进程退出", str(exc))
        raise SystemExit(1)

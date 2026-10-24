# -*- coding: utf-8 -*-
"""多个本机 Klook 项目共享出口时的跨进程互斥与熔断。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


DEFAULT_GUARD_ROOT = Path.home() / "PycharmProjects/.haicar-transfer-runtime/deployment/locks"


class KlookEgressGuardError(RuntimeError):
    """共享 Klook 出口正在使用或处于熔断状态。"""


def _guard_root() -> Path:
    configured = os.environ.get("KLOOK_EGRESS_GUARD_ROOT", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_GUARD_ROOT


def circuit_path() -> Path:
    return _guard_root() / "klook-egress-circuit.json"


def ensure_circuit_closed() -> None:
    path = circuit_path()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise KlookEgressGuardError("Klook 出口熔断文件无法安全读取，已阻止任务") from exc
    if isinstance(payload, dict) and payload.get("open"):
        state = str(payload.get("state") or "UNKNOWN")
        reason = str(payload.get("reason") or "未提供原因").splitlines()[0]
        raise KlookEgressGuardError(f"Klook 共享出口已熔断: {state}: {reason}")


def open_circuit(state: str, reason: str) -> Path:
    path = circuit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "open": True,
        "state": str(state or "UNKNOWN"),
        "reason": str(reason or "未提供原因")[:500],
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "price-data",
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class KlookEgressLock:
    def __init__(self):
        self.lock_dir = _guard_root() / "klook-egress.lock"
        self.owner_file = self.lock_dir / "owner.json"
        self._held = False

    def _clear_stale_lock(self) -> bool:
        try:
            payload = json.loads(self.owner_file.read_text(encoding="utf-8"))
            owner_pid = int(payload.get("pid") or 0)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return False
        if _pid_is_alive(owner_pid):
            return False
        try:
            self.owner_file.unlink()
            self.lock_dir.rmdir()
            return True
        except OSError:
            return False

    def acquire(self):
        ensure_circuit_closed()
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self.lock_dir.mkdir()
            except FileExistsError as exc:
                if attempt == 0 and self._clear_stale_lock():
                    continue
                raise KlookEgressGuardError(
                    "同一代理出口已有其他 Klook 任务运行，本次任务未启动"
                ) from exc
            payload = {
                "pid": os.getpid(),
                "project": "price-data",
                "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            self.owner_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._held = True
            return self
        raise KlookEgressGuardError("无法获取 Klook 共享出口锁")

    def release(self):
        if not self._held:
            return
        try:
            self.owner_file.unlink(missing_ok=True)
            self.lock_dir.rmdir()
        finally:
            self._held = False

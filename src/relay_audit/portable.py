"""Portable browser workbench with a visible lifetime and a private instance lock.

The executable directory owns ``data``. Bundled resources continue to be read
from the package directory, which may live inside PyInstaller's ``_internal``.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import webbrowser

from . import __version__

HOST = "127.0.0.1"
INSTANCE_HEADER = "X-API-MRI-Instance"
STARTUP_TIMEOUT = 90.0


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PortableError(RuntimeError):
    """A message safe to show without request details or credentials."""


def default_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[2] / "data"


def installation_id(data_dir: Path) -> str:
    entry = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve()
    return hashlib.sha256((str(entry).casefold() + "\0" + str(data_dir.resolve()).casefold()).encode("utf-8")).hexdigest()


def prepare_data_dir(path: Path) -> Path:
    path = path.resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / (".write-" + secrets.token_hex(8))
        with probe.open("xb") as stream:
            stream.write(b"API-MRI")
        probe.unlink()
        (path / "runs").mkdir(exist_ok=True)
        (path / "reference-library").mkdir(exist_ok=True)
    except OSError:
        raise PortableError("无法写入 data 文件夹。请把整个 API-MRI 文件夹移到可写的位置（如桌面），再启动。") from None
    return path


class InstanceLock:
    """OS-held byte lock: stale files cannot impersonate a running instance."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / ".portable.lock"
        self.stream: Any = None

    def acquire(self) -> bool:
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        self.stream = stream
        return True

    def close(self) -> None:
        if self.stream is not None:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


def _request_json(url: str, headers: dict[str, str] | None = None) -> dict:
    # Proxy environment settings must never send the loopback instance secret away.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(Request(url, headers=headers or {}), timeout=1.0) as response:
        if response.status != 200:
            raise ValueError("unexpected local response")
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("oversized local response")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("invalid local response")
    return result


def existing_instance(data_dir: Path, identity: str, *, timeout: float = STARTUP_TIMEOUT) -> str | None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            state_path = data_dir / ".portable-session.json"
            if state_path.stat().st_size > 4096:
                return None
            state = json.loads(state_path.read_text(encoding="utf-8"))
            port = state["port"]
            token = state["token"]
            if state["installation_id"] != identity:
                return None
            if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(token, str) or len(token) != 64:
                return None
            url = f"http://{HOST}:{port}"
            response = _request_json(url + "/api/portable/instance", {INSTANCE_HEADER: token})
            if response.get("installation_id") == identity and response.get("version") == state.get("version"):
                return url
        except (OSError, URLError, HTTPError, ValueError, KeyError, TypeError):
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.15)


def bind_loopback(preferred_port: int) -> socket.socket:
    if preferred_port != 0 and not 1024 <= preferred_port <= 65535:
        raise PortableError("端口应为 1024 至 65535，或使用 0 自动选择。")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        try:
            listener.bind((HOST, preferred_port))
        except OSError as exc:
            if preferred_port == 0 or exc.errno not in {errno.EADDRINUSE, errno.EACCES}:
                raise
            listener.bind((HOST, 0))
        listener.listen(128)
        listener.setblocking(False)
        return listener
    except OSError:
        listener.close()
        raise PortableError("无法创建回环连接，请检查系统网络设置后重试。") from None


class PortableServer:
    def __init__(self, data_dir: Path, identity: str, *, preferred_port: int = 8765):
        self.data_dir = data_dir
        self.identity = identity
        self.token = secrets.token_hex(32)
        self.listener = bind_loopback(preferred_port)
        self.port = self.listener.getsockname()[1]
        self.url = f"http://{HOST}:{self.port}"
        self.failure: BaseException | None = None
        self.server: Any = None
        self.app: Any = None
        self.thread: threading.Thread | None = None
        self._owns_state = False
        self._stopped = False

    def start(self, *, timeout: float = STARTUP_TIMEOUT) -> None:
        import uvicorn
        from fastapi import HTTPException, Request

        from .web import create_app

        self.app = create_app(self.data_dir / "runs", library_dir=self.data_dir / "reference-library")

        # A static token on disk is checked against the live process. Merely
        # finding an API-MRI-looking page at a familiar port is insufficient.
        async def instance(request: Request):
            if not secrets.compare_digest(request.headers.get(INSTANCE_HEADER, ""), self.token):
                raise HTTPException(403, "无效实例。")
            return {"installation_id": self.identity, "version": __version__}

        # FastAPI evaluates annotations in this module's globals. Request is
        # imported lazily so --help and error dialogs need no web dependencies.
        instance.__annotations__["request"] = Request
        self.app.add_api_route("/api/portable/instance", instance, methods=["GET"], include_in_schema=False)
        config = uvicorn.Config(self.app, host=HOST, port=self.port, loop="asyncio", http="h11", ws="none",
                                access_log=False, log_config=None, log_level="warning", timeout_graceful_shutdown=5)
        self.server = uvicorn.Server(config)

        def run() -> None:
            try:
                self.server.run(sockets=[self.listener])
            except BaseException as exc:
                self.failure = exc

        self.thread = threading.Thread(target=run, name="api-mri-server", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.thread.is_alive():
                raise PortableError("API-MRI 服务启动失败。请重新解压完整安装包后重试。")
            try:
                result = _request_json(self.url + "/api/portable/instance", {INSTANCE_HEADER: self.token})
                bootstrap = _request_json(self.url + "/api/bootstrap")
                if result.get("installation_id") == self.identity and bootstrap.get("version") == __version__:
                    self._save_state()
                    return
            except (OSError, URLError, ValueError):
                pass
            time.sleep(0.1)
        raise PortableError("启动等待超时。请检查安装包是否完整，关闭后重试。")

    def _save_state(self) -> None:
        state = {"pid": os.getpid(), "port": self.port, "token": self.token,
                 "installation_id": self.identity, "version": __version__}
        temporary = self.data_dir / ".portable-session.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self.data_dir / ".portable-session.json")
        self._owns_state = True

    def smoke_test(self) -> dict:
        bootstrap = _request_json(self.url + "/api/bootstrap")
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        sizes: dict[str, int] = {}
        for asset in ("/", "/app.js", "/app.css"):
            with opener.open(self.url + asset, timeout=5) as response:
                sizes[asset] = len(response.read())
                if response.status != 200 or sizes[asset] == 0:
                    raise PortableError("界面资源不完整，请重新解压安装包。")
        result = {"status": "passed", "version": __version__, "port": self.port,
                  "reference_packs": len(bootstrap.get("reference_packs", [])),
                  "identification_models": bootstrap.get("identification_bank", {}).get("model_count", 0),
                  "assets": sizes, "real_model_calls": 0}
        (self.data_dir / "smoke-test.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    def stop(self, *, timeout: float = 30.0) -> None:
        if self._stopped:
            return
        self._stopped = True
        deadline = time.monotonic() + timeout
        if self.app is not None:
            self.app.state.manager.close()
            self.app.state.reference_updater.close()
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(max(0, deadline - time.monotonic()))
        # A synchronous request may still be ending after cancellation. Give it
        # time to save its evidence, while the controller remains responsive.
        workers = list(self.app.state.manager.threads) if self.app is not None else []
        updater = getattr(self.app.state.reference_updater, "thread", None) if self.app is not None else None
        if updater is not None:
            workers.append(updater)
        for worker in workers:
            if worker.is_alive():
                worker.join(max(0, deadline - time.monotonic()))
        self.listener.close()
        if self._owns_state:
            try:
                (self.data_dir / ".portable-session.json").unlink(missing_ok=True)
            except OSError:
                pass
            self._owns_state = False


class Controller:
    """Small native lifetime control; the browser remains the workbench."""

    def __init__(self):
        import tkinter as tk
        from tkinter import font

        self.tk = tk
        self.root = tk.Tk()
        self.root.title("API-MRI")
        self.root.geometry("430x225")
        self.root.resizable(False, False)
        self.root.configure(bg="#f4f4ee")
        default_font = font.nametofont("TkDefaultFont")
        default_font.configure(family="Microsoft YaHei UI", size=10)
        tk.Label(self.root, text="API-MRI", font=("Bahnschrift", 23), bg="#f4f4ee", fg="#282c27").pack(anchor="w", padx=25, pady=(21, 6))
        self.status = tk.StringVar(value="正在启动…")
        tk.Label(self.root, textvariable=self.status, bg="#f4f4ee", fg="#282c27", wraplength=380,
                 justify="left", anchor="w").pack(fill="x", padx=26)
        self.address = tk.StringVar()
        tk.Label(self.root, textvariable=self.address, bg="#f4f4ee", fg="#5b675a", anchor="w").pack(fill="x", padx=26, pady=(7, 14))
        buttons = tk.Frame(self.root, bg="#f4f4ee")
        buttons.pack(fill="x", padx=25)
        self.open_button = tk.Button(buttons, text="打开工作台", bg="#98482e", fg="white", relief="flat", padx=16, pady=7, state="disabled")
        self.open_button.pack(side="left")
        self.exit_button = tk.Button(buttons, text="退出", bg="#e4e7dd", fg="#282c27", relief="flat", padx=16, pady=7)
        self.exit_button.pack(side="right")
        self.root.update()

    def run(self, server: PortableServer) -> None:
        self.status.set("已启动。关闭此窗口将停止服务。")
        self.address.set(server.url)
        self.open_button.configure(state="normal", command=lambda: webbrowser.open(server.url))
        stopping: threading.Thread | None = None

        def stop() -> None:
            nonlocal stopping
            if stopping is not None:
                return
            self.status.set("正在停止，已完成的记录会保留…")
            self.open_button.configure(state="disabled")
            self.exit_button.configure(state="disabled")
            stopping = threading.Thread(target=server.stop, name="api-mri-stop", daemon=True)
            stopping.start()
            poll_stop()

        def poll_stop() -> None:
            if stopping is not None and stopping.is_alive():
                self.root.after(100, poll_stop)
            else:
                self.root.destroy()

        def poll_server() -> None:
            if stopping is None and server.thread is not None and not server.thread.is_alive():
                self.status.set("服务已停止，请退出后重新启动。")
                self.open_button.configure(state="disabled")
            self.root.after(1000, poll_server)

        self.exit_button.configure(command=stop)
        self.root.protocol("WM_DELETE_WINDOW", stop)
        self.root.after(1000, poll_server)
        self.root.mainloop()

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass


def _show_error(message: str, *, headless: bool) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if not headless:
        try:
            from tkinter import messagebox

            messagebox.showerror("API-MRI", message)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="API-MRI 便携工作台")
    parser.add_argument("--data-dir", type=Path, default=None, help="记录与参考库的保存位置；默认程序旁 data 文件夹")
    parser.add_argument("--port", type=int, default=8765, help="首选端口；被占用时自动另选")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--headless", action="store_true", help="不显示控制窗口，使用 Ctrl+C 停止")
    parser.add_argument("--smoke-test", action="store_true", help="检查启动和资源后退出；结果写入 data/smoke-test.json")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("端口应为 1024 至 65535，或使用 0 自动选择。")
    # Some libraries print status even in a GUI executable. Never leave their
    # file descriptors as None, and never redirect those prints to a data log.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    lock: InstanceLock | None = None
    server: PortableServer | None = None
    controller: Controller | None = None
    try:
        data_dir = prepare_data_dir(args.data_dir or default_data_dir())
        identity = installation_id(data_dir)
        lock = InstanceLock(data_dir)
        if not lock.acquire():
            url = existing_instance(data_dir, identity)
            if url is None:
                raise PortableError("此 data 文件夹正被另一个实例使用，或上次启动尚未完成。请关闭已有 API-MRI 后重试。")
            if args.smoke_test:
                raise PortableError("便携版正在运行。请先退出，再运行启动自检。")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        if not args.headless and not args.smoke_test:
            controller = Controller()
        server = PortableServer(data_dir, identity, preferred_port=args.port)
        server.start()
        if args.smoke_test:
            print(json.dumps(server.smoke_test(), ensure_ascii=False))
            return 0
        if not args.no_browser:
            webbrowser.open(server.url)
        if controller is not None:
            controller.run(server)
        else:
            print(f"API-MRI: {server.url} (Ctrl+C to stop)", flush=True)
            while server.thread is not None and server.thread.is_alive():
                time.sleep(0.2)
        return 0
    except KeyboardInterrupt:
        return 0
    except PortableError as exc:
        _show_error(str(exc), headless=args.headless or args.smoke_test)
        return 1
    except Exception as exc:
        _show_error(f"启动失败（{type(exc).__name__}）。请重新解压完整安装包后重试。", headless=args.headless or args.smoke_test)
        return 1
    finally:
        if server is not None:
            server.stop()
        if controller is not None:
            controller.destroy()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())

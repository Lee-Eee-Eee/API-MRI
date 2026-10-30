import json
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError

import pytest

from relay_audit import __version__
from relay_audit.portable import (
    HOST, INSTANCE_HEADER, InstanceLock, PortableError, PortableServer,
    _request_json, bind_loopback, default_data_dir, existing_instance, installation_id, main,
    prepare_data_dir,
)


def test_data_stays_in_explicit_unicode_directory(tmp_path):
    data = prepare_data_dir(tmp_path / "便携 版本" / "data")
    assert data == (tmp_path / "便携 版本" / "data").resolve()
    assert (data / "runs").is_dir()
    assert (data / "reference-library").is_dir()
    assert not list(data.glob(".write-*"))
    assert installation_id(data) != installation_id(tmp_path / "other")


def test_frozen_default_ignores_extraction_and_working_directory(tmp_path, monkeypatch):
    executable = tmp_path / "移动后的 程序" / "API-MRI.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "temporary-extraction"), raising=False)
    assert default_data_dir() == executable.parent.resolve() / "data"


def test_unwritable_data_has_actionable_error(tmp_path, monkeypatch):
    original = Path.open

    def deny_probe(path, *args, **kwargs):
        if path.name.startswith(".write-"):
            raise PermissionError("private-test-secret")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_probe)
    with pytest.raises(PortableError, match="可写的位置") as error:
        prepare_data_dir(tmp_path / "data")
    assert "private-test-secret" not in str(error.value)


def test_os_lock_rejects_second_launcher_and_releases(tmp_path):
    first = InstanceLock(tmp_path)
    second = InstanceLock(tmp_path)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.close()
    assert second.acquire()
    second.close()


def test_occupied_port_chooses_other_loopback_port():
    busy = socket.socket()
    busy.bind((HOST, 0))
    busy.listen()
    try:
        other = bind_loopback(busy.getsockname()[1])
        try:
            assert other.getsockname()[0] == HOST
            assert other.getsockname()[1] != busy.getsockname()[1]
        finally:
            other.close()
    finally:
        busy.close()


def test_private_live_instance_and_smoke_resources(tmp_path):
    data = prepare_data_dir(tmp_path / "移动 路径" / "data")
    identity = installation_id(data)
    server = PortableServer(data, identity, preferred_port=0)
    try:
        server.start(timeout=30)
        assert existing_instance(data, identity, timeout=0) == server.url
        assert existing_instance(data, "another-package", timeout=0) is None
        with pytest.raises(HTTPError) as denied:
            _request_json(server.url + "/api/portable/instance", {INSTANCE_HEADER: "wrong"})
        assert denied.value.code == 403
        # An unrelated listener could redirect a probe to another origin. The
        # private instance token must never follow even a same-host redirect.
        from fastapi.responses import RedirectResponse

        server.app.add_api_route("/api/portable/redirect", lambda: RedirectResponse("/api/portable/instance", status_code=302))
        with pytest.raises(HTTPError) as redirected:
            _request_json(server.url + "/api/portable/redirect", {INSTANCE_HEADER: server.token})
        assert redirected.value.code == 302
        result = server.smoke_test()
        assert result["status"] == "passed"
        assert result["version"] == __version__
        assert result["reference_packs"] >= 1
        assert result["identification_models"] >= 100
        assert all(size > 100 for size in result["assets"].values())
        saved = (data / "smoke-test.json").read_text(encoding="utf-8")
        assert server.token not in saved
        assert "session_token" not in saved
        assert json.loads(saved)["real_model_calls"] == 0
        # A different service on the same port cannot pass the private check.
        state_path = data / ".portable-session.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["token"] = "a" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        assert existing_instance(data, identity, timeout=0) is None
    finally:
        server.stop(timeout=5)
    assert server.thread is not None and not server.thread.is_alive()
    assert not (data / ".portable-session.json").exists()
    assert existing_instance(data, identity, timeout=0) is None


def test_main_smoke_keeps_report_and_releases_lock(tmp_path):
    data = tmp_path / "自检目录" / "data"
    assert main(["--smoke-test", "--headless", "--port", "0", "--data-dir", str(data)]) == 0
    assert json.loads((data / "smoke-test.json").read_text(encoding="utf-8"))["status"] == "passed"
    lock = InstanceLock(data)
    assert lock.acquire()
    lock.close()
    assert not (data / ".portable-session.json").exists()

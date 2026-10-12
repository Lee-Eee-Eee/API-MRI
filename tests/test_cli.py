import json
import socket

import pytest

from relay_audit.cli import main
from relay_audit.records import AuditRecord, content_hash, file_hash, save_records


def test_offline_cli_replays_without_network_and_preserves_existing_evidence(tmp_path, monkeypatch):
    paths = {}
    for endpoint, response in (("reference", "blue"), ("candidate", "magenta")):
        paths[endpoint] = tmp_path / f"{endpoint}.jsonl"
        save_records(paths[endpoint], [
            AuditRecord(
                run_id=endpoint, endpoint_id=endpoint, prompt_id="p1", replicate=index,
                prompt_hash=content_hash("same-prompt"), config_hash=content_hash("same-config"),
                model_declared="fixture-model", reference_kind="synthetic_control", synthetic=True,
                status="success", response=response,
                metadata={"expected_prompt_ids": ["p1"], "expected_repeats": 12},
            )
            for index in range(12)
        ])
    before = {name: file_hash(path) for name, path in paths.items()}

    def forbidden_connection(*args, **kwargs):
        raise AssertionError("Offline comparison must not connect to a server")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connection)
    common = ["compare", "--reference", str(paths["reference"]), "--candidate", str(paths["candidate"]),
              "--permutations", "99"]
    out = tmp_path / "first"
    assert main(common + ["--out", str(out)]) == 0
    first = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert first["status"] == "behavior_difference" and first["synthetic"] is True
    assert (out / "report.md").is_file()

    replay = tmp_path / "replay"
    assert main(common + ["--out", str(replay)]) == 0
    second = json.loads((replay / "report.json").read_text(encoding="utf-8"))
    assert first == second
    report_before = file_hash(out / "report.json")
    with pytest.raises(SystemExit) as error:
        main(common + ["--out", str(out)])
    assert error.value.code == 2
    assert file_hash(out / "report.json") == report_before
    assert {name: file_hash(path) for name, path in paths.items()} == before

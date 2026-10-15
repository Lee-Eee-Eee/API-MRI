import json
import socket

import pytest

from relay_audit.cli import main


def test_reference_audit_is_offline_and_does_not_invent_accuracy(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Reference audit must stay offline")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    out = tmp_path / "inventory"
    assert main(["reference-audit", "--library-dir", str(tmp_path / "empty"), "--out", str(out)]) == 0
    result = json.loads((out / "reference-audit.json").read_text(encoding="utf-8"))
    assert result["external_model_calls"] == 0
    assert result["empirical_accuracy"] is None
    assert result["summary"] == {
        "pack_count": 26, "packs_with_recorded_endpoint": 7,
        "packs_with_independently_verified_source": 0,
        "packs_with_multiple_self_runs": 0, "packs_with_other_model_runs": 0,
        "unusable_result_entries": 0,
    }
    gpt = next(r for r in result["references"] if r["id"] == "gpt-4.1-mini_20260915")
    assert gpt["plans"]["screening"]["max_requests"] <= 4
    assert gpt["plans"]["screening"]["probe_count"] <= 30
    assert gpt["plans"]["full"]["probe_count"] == 265
    assert gpt["plans"]["full"]["max_requests"] == 108
    before = (out / "reference-audit.json").read_bytes()
    with pytest.raises(SystemExit):
        main(["reference-audit", "--out", str(out)])
    assert before == (out / "reference-audit.json").read_bytes()


def test_incomplete_extra_results_do_not_count_as_retained_comparisons(monkeypatch):
    from relay_audit import reference_audit as audit
    from relay_audit.reference_library import load_reference
    meta, data = load_reference("gpt-4.1-mini_20260915")
    data["target_results"].update({
        "empty": None,
        "label-only": {"is_self": False, "model": "unverified"},
        "bad-vector": {"is_self": False, "values": [True] * len(data["probes"])},
        "has-vector": {"is_self": False, "values": [1.0] * len(data["probes"])},
    })
    monkeypatch.setattr(audit, "list_reference_packs", lambda _: [meta])
    monkeypatch.setattr(audit, "load_reference", lambda *args: (meta, data))
    result = audit.audit_references()
    assert result["summary"]["packs_with_other_model_runs"] == 1
    assert result["references"][0]["retained_other_model_runs"] == 1
    assert result["summary"]["unusable_result_entries"] == 3

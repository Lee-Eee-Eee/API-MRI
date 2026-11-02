from relay_audit import features
from relay_audit.data_registry import data_status
from relay_audit.web import create_app,validate_job
from fastapi.testclient import TestClient
import pytest

def test_core_keeps_everyday_modes_and_guards_optional_research(tmp_path,monkeypatch):
    monkeypatch.setattr(features,'find_spec',lambda name:None)
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8765') as client:
        boot=client.get('/api/bootstrap').json()
        assert boot['capabilities']['research_extensions'] is False
        assert {'service','identify','fingerprint','showcase'}<=set(boot['capabilities']['modes'])
        assert 'demo' not in boot['capabilities']['modes'] and boot['scenarios']==[]
        assert client.get('/api/data-status').json()['network_requests']==0
    with pytest.raises(ValueError,match='研究扩展'):validate_job({'mode':'demo'})

def test_fingerprint_status_reports_collection_instead_of_build_date():
    data=data_status()
    bank=next(x for x in data['entries'] if x['id']=='fingerprint')
    assert bank['date']=='2026-07-08'
    assert len(bank['sha256'])==64
    assert {x['id'] for x in data['entries']}=={'fingerprint','models','examples','benchmarks'}

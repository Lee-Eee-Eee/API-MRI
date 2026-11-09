import json
import time

from fastapi.testclient import TestClient

from relay_audit import fingerprint as fp
from relay_audit import identification_library as library
from relay_audit.web import create_app


def wait(client, identifier):
    for _ in range(1000):
        job = client.get('/api/runs/' + identifier).json()
        if job['status'] in {'completed', 'failed', 'cancelled'}:
            return job
        time.sleep(.01)
    raise AssertionError('Reference task timed out')


def test_capture_publish_compare_and_empty_library_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(library,'PUBLIC_INDEX_PATH',tmp_path/'no-bundled-index.json')
    cells = library.core_cells()
    original = library.legacy_bank()
    answers = {}
    for cell in cells:
        value = next(iter(original['models'][0]['counts'][cell['id']]))
        if cell['normalize_as'] == 'binary':
            value = '正面' if value == 'h' else '反面'
        answers[cell['user']] = value
    calls = []
    def call(client, config, key, body, secrets, cancelled=None):
        calls.append(body)
        return {'http_status':200,'error':None,'elapsed_seconds':.01,'response_headers':{},
            'raw_response':{'choices':[{'message':{'role':'assistant','content':answers[body['messages'][-1]['content']]},'finish_reason':'stop'}],
                            'usage':{'prompt_tokens':12,'completion_tokens':2,'total_tokens':14}}}
    monkeypatch.setattr(fp, '_call', call)
    root = tmp_path/'first'
    app = create_app(root/'runs',library_dir=root/'library')
    with TestClient(app,base_url='http://127.0.0.1') as client:
        boot = client.get('/api/bootstrap').json()
        headers = {'x-relay-token':boot['session_token']}
        payload = {'mode':'reference_collect','candidate':{'api_base':'https://example.test/v1','model':'fresh-alias','api_key':'not-a-real-secret'},
                   'source':{'kind':'operator_declared','label':'Fresh service','provider':'test-service','model_alias':'fresh-alias'}}
        plan = client.post('/api/plan',headers=headers,json=payload)
        assert plan.status_code == 200 and plan.json()['maximum_requests'] == 96
        assert calls == []
        assert client.post('/api/identification-library/activate',json={'bank_id':'legacy'}).status_code == 403
        started = client.post('/api/runs',headers=headers,json=payload)
        assert started.status_code == 202
        job = wait(client,started.json()['id'])
        assert job['status'] == 'completed', job
        assert job['reports'][0]['report']['reference']['can_publish']
        assert len(calls) == 96
        evidence = client.get(f"/api/runs/{job['id']}/download/reference/jsonl")
        assert evidence.status_code == 200 and 'not-a-real-secret' not in evidence.text
        assert len(client.get(f"/api/runs/{job['id']}/records?report=reference").json()['reference']) == 98
        for _ in range(100):
            if app.state.manager.active is None:
                break
            time.sleep(.01)
        published = client.post('/api/identification-library/publish',headers=headers,json={'run_id':job['id']})
        assert published.status_code == 200, published.text
        bank_id = published.json()['bank_id']
        repeat = client.post('/api/identification-library/publish',headers=headers,json={'run_id':job['id']})
        assert repeat.json()['bank_id'] == bank_id and len(calls) == 96
        current = client.get('/api/identification-library',params={'model':'fresh-alias'}).json()
        assert current['active_bank_id'] == bank_id
        assert current['bank']['model_count'] == 133
        assert current['coverage']['match'] == 'unconfirmed_alias'
        assert current['coverage']['model_id']
        assert client.get('/api/bootstrap').json()['identification_bank']['id'] == bank_id
        identify = {'mode':'identify','bank_id':bank_id,'candidate':payload['candidate'],
                    'budget':{'max_requests':12,'max_output_tokens':192}}
        started = client.post('/api/runs',headers=headers,json=identify)
        assert started.status_code == 202, started.text
        comparison = wait(client,started.json()['id'])
        assert comparison['status'] == 'completed', comparison
        report = comparison['reports'][0]['report']
        assert report['status'] == 'reference_shortlist'
        assert report['identity_decision'] == 'abstained'
        raw = client.get(f"/api/runs/{comparison['id']}/download/identification/jsonl").text
        assert json.loads(raw.splitlines()[0])['bank_snapshot']['id'] == bank_id
        before = len(calls)
        rerun = client.post('/api/identification-library/recompare',headers=headers,
            json={'run_id':comparison['id'],'bank_id':'legacy'})
        assert rerun.status_code == 202, rerun.text
        reordered = wait(client,rerun.json()['id'])
        assert reordered['status'] == 'completed', reordered
        rereport = reordered['reports'][0]['report']
        assert rereport['reanalysis']['model_api_calls'] == 0
        assert rereport['cost']['real_requests'] == 0
        reraw = client.get(f"/api/runs/{reordered['id']}/download/identification/jsonl").text
        assert json.loads(reraw)['schema'] == 'api-mri-identification-recompare/1'
        assert len(calls) == before
        again = client.post('/api/identification-library/recompare',headers=headers,
            json={'run_id':reordered['id'],'bank_id':bank_id})
        assert again.status_code == 202, again.text
        assert wait(client,again.json()['id'])['status'] == 'completed'
        assert len(calls) == before
    with TestClient(create_app(tmp_path/'second'),base_url='http://127.0.0.1') as client:
        headers={'x-relay-token':client.get('/api/bootstrap').json()['session_token']}
        for text, expected in [(raw, report), (reraw, rereport)]:
            started=client.post('/api/runs',headers=headers,json={'mode':'identify_offline','evidence_jsonl':text})
            assert started.status_code == 202, started.text
            replay=wait(client,started.json()['id'])
            assert replay['reports'][0]['report'] == expected
            assert len(calls) == before

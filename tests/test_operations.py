import json
import shutil
from pathlib import Path

import pytest

from freelahunter.operations import OperationsStore, assess
from freelahunter.operator_panel import HunterProcessManager, create_operator_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    instance = OperationsStore(tmp_path)
    yield instance
    instance.close()


def snapshot(**changes):
    return dict({'url': 'https://www.99freelas.com.br/project/api-123', 'platform': '99freelas',
                 'title': 'API Python', 'description': 'Desenvolver API Python com documentação fornecida e testes de aceite em 10 dias.',
                 'currency': 'BRL', 'budget_max': 2000}, **changes)


def seed(store):
    job = snapshot()
    return store.save(job, store.analysis_key(job), {'action': 'proposal', 'message': 'Proposta inicial'}, assess(job, store.filters()))


def test_cache_invalidates_when_scope_or_filter_changes(store):
    job = snapshot()
    key = store.analysis_key(job)
    seed(store)
    assert store.cached(job, key)['draft']['message'] == 'Proposta inicial'
    changed = snapshot(description='Novo escopo: aplicativo mobile com pagamentos.')
    assert store.cached(changed, store.analysis_key(changed)) is None
    path = store.root / 'config/hunt_filters.json'
    rules = store.filters()
    rules['minimum_score'] += 1
    path.write_text(json.dumps(rules))
    assert store.cached(job, store.analysis_key(job)) is None


def test_reanalysis_preserves_manual_text_and_outcome(store):
    row = seed(store)
    edited = store.update(row['id'], {'revision': row['revision'], 'message': 'Texto revisado', 'outcome': 'negotiating'})
    assert edited['revision'] == 2
    row = store.save(snapshot(), 'changed', {'action': 'proposal', 'message': 'Nova geração'}, assess(snapshot(), store.filters()))
    assert row['message'] == 'Texto revisado'
    assert row['generated_message'] == 'Nova geração'
    assert row['outcome'] == 'negotiating'
    with pytest.raises(RuntimeError):
        store.update(row['id'], {'revision': 1, 'message': 'Edição desatualizada'})


def test_filters_explain_exclusions_and_unknown_budget(store):
    rules = store.filters()
    assert assess(snapshot(budget_max=50), rules)['eligible'] is False
    assert 'abaixo' in assess(snapshot(budget_max=50), rules)['exclusions'][0]
    result = assess(snapshot(budget_max=None), rules)
    assert result['eligible'] is True
    assert any('não informado' in item for item in result['reasons'])
    result = assess(snapshot(title='Tradução', description='Traduzir documentos e revisar textos literários.'), rules)
    assert result['eligible'] is False
    assert any('tecnologia' in item for item in result['exclusions'])


def test_outcome_validation_and_summary(store):
    row = seed(store)
    with pytest.raises(ValueError):
        store.update(row['id'], {'revision': 1, 'outcome': 'unknown'})
    store.update(row['id'], {'revision': 1, 'outcome': 'won', 'notes': 'Contrato fechado manualmente'})
    assert store.listing()['summary']['won'] == 1
    assert store.listing()['summary']['conversion_rate'] == 1


def test_panel_api_roundtrip_and_conflict(store):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    row = seed(store)
    manager = HunterProcessManager(store.root)
    client = TestClient(create_operator_app(manager), base_url='http://127.0.0.1:8765')
    assert client.get('/api/review').json()['items'][0]['id'] == row['id']
    assert client.get('/review.js').status_code == 200
    assert 'Rascunhos e resultados' in client.get('/').text
    assert client.post('/api/review/' + row['id'], json={'revision': 1, 'message': 'Revisada'}).status_code == 200
    assert client.post('/api/review/' + row['id'], json={'revision': 1, 'message': 'Conflito'}).status_code == 409
    assert client.get('/api/review/missing').status_code == 404


def test_generator_reuses_analysis_and_preserves_review(store, monkeypatch, capsys):
    import io
    import sys
    import scripts.generate_proposal as generator

    original = generator.build_quote
    calls = []
    monkeypatch.setattr(generator, 'OperationsStore', lambda root: OperationsStore(store.root))
    monkeypatch.setattr(generator, 'build_quote', lambda job, profile: (calls.append(job['description']), original(job, profile))[1])
    monkeypatch.setenv('HUNT_ANALYSIS_CACHE', 'true')

    def generate(job):
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(job)))
        generator.main()
        return json.loads(capsys.readouterr().out)

    job = snapshot()
    first = generate(job)
    assert first['cache_hit'] is False
    row = store.get(first['opportunity_id'])
    store.update(row['id'], {'revision': row['revision'], 'message': 'Minha revisão'})
    second = generate(job)
    assert second['cache_hit'] is True
    assert second['reviewed'] is True
    assert len(calls) == 1
    third = generate(snapshot(description=job['description'] + ' Incluir autenticação.'))
    assert third['cache_hit'] is False
    assert third['reviewed'] is True
    assert len(calls) == 2

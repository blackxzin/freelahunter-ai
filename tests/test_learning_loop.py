"""Outcomes recorded anywhere feed reports, style selection and job ordering."""
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from freelahunter.core import Database, stable_proposal_key
from freelahunter.operations import OperationsStore, assess, order_links, sync_tracking_outcome, type_response_rates

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.99freelas.com.br/project/api-123'


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_PATH', raising=False)
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    return tmp_path


def sent_proposal(db, url=URL, variant='A', project_type='API', outcome=None, n=0):
    key = stable_proposal_key(url)
    proposal_id = db.register_proposal(None, key, 'Proposta', f'mensagem {n}', 1000, project_type,
                                       f'client-{n}', template_variant=variant)
    db.mark_sent(key, f'ext-{n}')
    if outcome:
        db.conn.execute('UPDATE proposals SET outcome_status=? WHERE id=?', (outcome, proposal_id))
        db.conn.commit()
    return proposal_id


def test_panel_outcome_is_mirrored_into_tracking_database(root):
    db = Database(str(root / 'freelahunter.db'), environ={})
    proposal_id = sent_proposal(db)
    store = OperationsStore(root)
    job = {'url': URL, 'platform': '99freelas', 'title': 'API Python',
           'description': 'Desenvolver API Python com documentação e testes de aceite.', 'currency': 'BRL'}
    row = store.save(job, 'k', {'action': 'proposal', 'message': 'x'}, assess(job, store.filters()))
    store.update(row['id'], {'revision': row['revision'], 'outcome': 'won'})
    store.close()
    status, = db.conn.execute('SELECT outcome_status FROM proposals WHERE id=?', (proposal_id,)).fetchone()
    assert status == 'accepted'
    events = [r[0] for r in db.conn.execute('SELECT event FROM proposal_events WHERE proposal_id=?', (proposal_id,))]
    assert 'outcome_accepted' in events


def test_panel_sync_ignores_unsent_or_missing_tracking(root):
    assert sync_tracking_outcome(root, URL, 'won') is False  # no tracking DB: nothing created
    assert not (root / 'freelahunter.db').exists()
    db = Database(str(root / 'freelahunter.db'), environ={})
    db.register_proposal(None, stable_proposal_key(URL), 'Proposta', 'm', 1000)
    assert sync_tracking_outcome(root, URL, 'won') is False  # never sent
    assert sync_tracking_outcome(root, URL, 'draft') is False


def test_style_and_project_stats_count_only_sent_proposals(tmp_path):
    db = Database(str(tmp_path / 't.db'), environ={})
    sent_proposal(db, 'https://x/1', 'A', 'API', 'responded', 1)
    sent_proposal(db, 'https://x/2', 'A', 'API', None, 2)
    sent_proposal(db, 'https://x/3', 'B', 'landing page', 'accepted', 3)
    db.register_proposal(None, 'draft-only', 'Proposta', 'm', 1000, template_variant='C')
    assert db.style_stats() == {'A': {'sent': 2, 'responded': 1}, 'B': {'sent': 1, 'responded': 1}}
    assert db.project_type_stats()['API/backend'] == {'sent': 2, 'responded': 1, 'accepted': 0}
    variants = {row[0]: row[1:] for row in db.conversion_report()['variants']}
    assert variants['A'] == (2, 1, 0)


def test_variant_survives_regeneration_after_send(tmp_path):
    db = Database(str(tmp_path / 'v.db'), environ={})
    sent_proposal(db, variant='A')
    db.register_proposal(None, stable_proposal_key(URL), 'Proposta', 'texto novo', 1000, template_variant='C')
    assert db.conn.execute('SELECT template_variant FROM proposals').fetchone()[0] == 'A'


def test_tracking_cli_registers_variant_and_reports_it(tmp_path):
    database = tmp_path / 'cli.db'
    def run(*args, payload=None):
        done = subprocess.run([sys.executable, 'scripts/proposal_tracking.py', '--database', str(database), *args],
                              input=json.dumps(payload) if payload is not None else None,
                              text=True, capture_output=True, check=True)
        return json.loads(done.stdout.strip().splitlines()[-1])
    registered = run('register', payload={'url': URL, 'title': 'API', 'message': 'm', 'price': 1000,
                                          'template_variant': 'B'})
    run('sent', '--proposal-id', str(registered['proposal_id']), '--external-id', 'e1')
    report = run('report')
    assert report['variants'] == [{'variant': 'B', 'sent': 1, 'responded': 0, 'accepted': 0, 'response_rate': 0}]


def test_order_puts_unseen_jobs_with_better_history_first(root):
    db = Database(str(root / 'freelahunter.db'), environ={})
    for n in range(5):
        sent_proposal(db, f'https://x/l{n}', 'A', 'landing page', 'responded', n)
        sent_proposal(db, f'https://x/a{n}', 'A', 'API', None, 10 + n)
    db.conn.close()
    rates = type_response_rates(root)
    assert rates == {'landing page': 1.0, 'API/backend': 0.0}
    links = [{'href': 'https://w/seen', 'text': 'Landing page antiga'},
             {'href': 'https://w/api', 'text': 'Criar API REST'},
             {'href': 'https://w/other', 'text': 'Projeto diverso'},
             {'href': 'https://w/lp', 'text': 'Landing page para clínica'}]
    ordered = order_links(links, {'https://w/seen': '2026-01-01'}, rates)
    assert [row['href'] for row in ordered] == ['https://w/lp', 'https://w/api', 'https://w/other', 'https://w/seen']


def test_order_keeps_listing_order_without_history(root):
    links = [{'href': f'https://w/{n}', 'text': 'API'} for n in range(4)]
    assert order_links(links, {}, type_response_rates(root)) == links


def test_generator_enriches_snapshot_and_explains_category_history(root, monkeypatch):
    import scripts.generate_proposal as generator
    db = Database(str(root / 'freelahunter.db'), environ={})
    for n in range(5):
        sent_proposal(db, f'https://x/{n}', 'A', 'API', 'responded' if n < 2 else None, n)
    db.record_message('conv-1', 'Prazo máximo de 10 dias.', proposal_id=1, is_scope_context=True)
    found = generator.tracking_database(root)
    snapshot = generator.enrich({'title': 'API Python', 'client_key': 'client-0'}, found)
    assert snapshot['client_history']['known'] is True
    assert snapshot['confirmed_context'] == ['Prazo máximo de 10 dias.']
    explicit = generator.enrich({'client_key': 'client-0', 'client_history': {'x': 1}}, found)
    assert explicit['client_history'] == {'x': 1}  # caller-provided data wins
    assert generator.category_reason({'title': 'API Python'}, found) == 'Histórico em API/backend: 2/5 respostas (40%), 0 fechadas.'
    assert generator.category_reason({'title': 'Landing page'}, found) is None
    monkeypatch.setenv('DATABASE_PATH', str(root / 'missing.db'))
    assert generator.tracking_database(root) is None

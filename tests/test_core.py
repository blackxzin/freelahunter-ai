import json
import threading
from io import BytesIO

import pytest

from freelahunter.core import *
from freelahunter.providers import HttpJobProvider

def job(): return Job('Landing page', 'Criar uma landing page responsiva com apresentação dos serviços, depoimentos e contato. Fornecemos layout aprovado, textos e imagens finais. A entrega inclui HTML e CSS, publicação na hospedagem do cliente e uma rodada de revisão com comparação visual em desktop e celular.', external_id='1', url='https://x/1', skills=['Python','FastAPI','SQL'], budget_min=500, budget_max=20000)

def test_authorized_autosend_is_idempotent():
    db=Database(':memory:'); sender=MockSender(); p=AuthorizedMockProvider([job()])
    first=run_pipeline(p,db,auto_send=True,dry_run=False,kill_switch=False,sender=sender)
    second=run_pipeline(p,db,auto_send=True,dry_run=False,kill_switch=False,sender=sender)
    assert first['sent']==1 and second['duplicates']==1 and len(sender.sent)==1

def test_safety_guards_block_send():
    for kwargs in ({'dry_run':True},{'auto_send':False},{'kill_switch':True}):
        sender=MockSender()
        opts={'auto_send':True,'dry_run':False,'kill_switch':False,'sender':sender}; opts.update(kwargs)
        db=Database(':memory:'); s=run_pipeline(AuthorizedMockProvider([job()]),db,**opts)
        assert s['sent']==0 and not sender.sent

def test_unauthorized_provider_never_sends():
    sender=MockSender(); s=run_pipeline(MockProvider([job()]),Database(':memory:'),auto_send=True,dry_run=False,kill_switch=False,sender=sender)
    assert s['sent']==0

def test_validator_blocks_placeholders():
    p=ProposalDraft(1,'x','TODO [CLIENT]',1,None,[])
    assert ProposalValidator().validate(p,Profile()) [0]=='FAILED'

def test_profile_loads_team_and_client_cost_rules(tmp_path):
    profile_path = tmp_path / 'profile.yaml'
    profile_path.write_text(
        'headline: Desenvolvedor Full Stack\n'
        'team_description: equipe de dois desenvolvedores full stack\n'
        'client_pays_paid_services: true\n'
        'education: ""\n',
        encoding='utf-8',
    )

    profile = ProfileService(str(profile_path)).load()
    proposal = ProposalGenerator().generate(job(), AIService().analyze(job(), profile, []), profile, [])

    assert 'estudante' not in profile.headline.lower()
    assert profile.team_description == 'equipe de dois desenvolvedores full stack'
    assert profile.client_pays_paid_services is True
    assert 'equipe de dois desenvolvedores full stack' in proposal.message
    assert 'contas do cliente' in proposal.message


def test_pipeline_applies_profile_job_rules():
    profile = Profile(
        preferred_jobs=['API'],
        excluded_jobs=['Premium'],
        minimum_budget=500,
    )
    jobs = [
        Job('API pública', 'Criar API REST', external_id='good', url='https://mock/good', budget_max=800),
        Job('API Premium', 'Criar API Premium', external_id='premium', url='https://mock/premium', budget_max=800),
    ]

    stats = run_pipeline(MockProvider(jobs), Database(':memory:'), profile=profile)

    assert stats['found'] == 2
    assert stats['filtered'] == 1
    assert stats['proposals'] == 1


def test_filter_matches_whole_keywords_and_accents():
    service = FilterService(included_keywords=['API'], excluded_keywords=['Premium'])
    assert service.accepts(Job('Capital de giro', 'Sistema web', external_id='capital')) is False
    assert service.accepts(Job('API pública', 'Integração com automação', external_id='api')) is True
    assert service.accepts(Job('API premiumizada', 'API REST', external_id='premiumized')) is True


def test_analysis_uses_skills_found_in_description_when_feed_omits_skills():
    analyzed = AIService().analyze(
        Job('API FastAPI', 'Criar API com Python, FastAPI e SQL.', external_id='text-only'),
        ProfileService().load(), [],
    )
    assert {'python', 'fastapi'} <= set(analyzed.matched_skills)
    assert analyzed.score > 50


def test_database_migration_preserves_proposals_and_tracks_outcome(tmp_path):
    db = Database(str(tmp_path / 'tracking.db'))
    proposal_id = db.register_proposal(None, 'key-1', 'Proposta', 'mensagem', 1200, 'API', 'client-1')
    db.mark_sent('key-1', 'external-1', '42')
    db.record_message('42', 'Confirmo duas telas.', is_scope_context=True)
    db.set_proposal_outcome(proposal_id, 'accepted')
    row = db.conn.execute('SELECT outcome_status,conversation_id,price_band FROM proposals WHERE id=?', (proposal_id,)).fetchone()
    assert row == ('accepted', '42', 'R$ 1.000–2.499')
    assert db.conn.execute('SELECT COUNT(*) FROM conversation_messages').fetchone()[0] == 1
    assert db.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_dynamic_quote_uses_complexity_urgency_and_history():
    from freelahunter.quoting import build_quote
    profile = ProfileService().load()
    base = build_quote({'title': 'API', 'description': 'Criar API simples.'}, profile)
    adjusted = build_quote({'title': 'API', 'description': 'Criar API com quatro integrações e seis telas.',
                            'integrations_count': 4, 'screens_count': 6, 'deadline_days': 2,
                            'client_history': {'recurring': True, 'good_history': True}}, profile)
    assert adjusted['pricing_factors']['multiplier'] > 1
    assert adjusted['price_range'][1] > base['price_range'][1]


def test_fallback_message_has_team_price_and_negotiation_language():
    from freelahunter.quoting import build_quote
    draft = build_quote({
        'title': 'API de pedidos',
        'description': 'Criar API REST com Python, autenticação e integração com gateway de pagamento.',
    }, ProfileService().load())
    assert draft['fallback_price'] is not None
    assert 'equipe de dois desenvolvedores full stack' in draft['fallback_message']
    assert 'preço pode ser negociado' in draft['fallback_message']
    assert 'R$ ' in draft['fallback_message']


def test_client_identity_is_normalized_for_duplicate_protection(tmp_path):
    db = Database(str(tmp_path / 'normalized-client.db'))
    db.register_proposal(None, 'client-url-key', 'Proposta', 'mensagem', 1000, 'API',
                         'https://www.example.test/users/42/?ref=profile')
    db.mark_sent('client-url-key', 'external-1')
    assert db.client_send_gate('https://WWW.EXAMPLE.TEST/users/42') == (
        False, 'limite por cliente atingido (1/1 em 24h)'
    )


def test_concurrent_workers_cannot_reserve_two_clients(tmp_path):
    path = str(tmp_path / 'client-claim.db')
    first = Database(path)
    second = Database(path)
    first.register_proposal(None, 'client-key-a', 'A', 'mensagem', 1000, 'API', 'client-42')
    second.register_proposal(None, 'client-key-b', 'B', 'mensagem', 1000, 'API', 'client-42')
    barrier = threading.Barrier(2)
    results = []

    def claim(db, key):
        barrier.wait(timeout=2)
        results.append(db.claim_send(key))

    workers = [threading.Thread(target=claim, args=(first, 'client-key-a')),
               threading.Thread(target=claim, args=(second, 'client-key-b'))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)
    assert sorted(results) == [False, True]


def test_pause_circuit_blocks_after_rejections(tmp_path):
    db = Database(str(tmp_path / 'pause.db'), environ={})
    for number in range(5):
        proposal_id = db.register_proposal(None, f'key-{number}', 'Proposta', 'mensagem', 1000, 'API', 'client')
        db.mark_sent(f'key-{number}', f'ext-{number}')
        db.set_proposal_outcome(proposal_id, 'rejected')
        # Two early rejections are noise, not a trend.
        assert db.get_automation_state()[0] == (1 if number == 4 else 0)
    assert db.send_gate()[0] is False


def test_rejection_minimum_sample_comes_from_environment(tmp_path):
    db = Database(str(tmp_path / 'pause-env.db'), environ={'PAUSE_MIN_DECIDED': '2'})
    for number in range(2):
        proposal_id = db.register_proposal(None, f'key-{number}', 'Proposta', 'mensagem', 1000, 'API', 'client')
        db.mark_sent(f'key-{number}', f'ext-{number}')
        db.set_proposal_outcome(proposal_id, 'rejected')
    assert db.get_automation_state()[0] == 1


def _old_sent(db, count, prefix='k', start_day=1):
    for n in range(count):
        db.register_proposal(None, f'{prefix}{n}', 'Proposta', 'm', 1000)
        db.mark_sent(f'{prefix}{n}', f'e{prefix}{n}')
        db.conn.execute('UPDATE proposals SET sent_at=? WHERE idempotency_key=?',
                        (f'2020-01-{start_day + n:02d}T00:00:00+00:00', f'{prefix}{n}'))
    db.conn.commit()


def test_recent_reply_breaks_no_response_streak(tmp_path):
    db = Database(str(tmp_path / 'streak.db'), environ={})
    _old_sent(db, 5)
    newest = db.conn.execute("SELECT id FROM proposals WHERE idempotency_key='k4'").fetchone()[0]
    db.conn.execute("UPDATE proposals SET outcome_status='responded' WHERE id=?", (newest,))
    db.conn.commit()
    assert db.evaluate_pause() is False


def test_streak_ignores_old_silence_followed_by_replies(tmp_path):
    db = Database(str(tmp_path / 'streak-old.db'), environ={})
    _old_sent(db, 5, 'old', 1)
    _old_sent(db, 2, 'new', 20)
    db.conn.execute("UPDATE proposals SET outcome_status='responded' WHERE idempotency_key LIKE 'new%'")
    db.conn.commit()
    assert db.evaluate_pause() is False


def test_client_gate_blocks_repeat_within_24_hours(tmp_path):
    db = Database(str(tmp_path / 'client-limit.db'))
    proposal_id = db.register_proposal(None, 'client-key-1', 'Proposta', 'mensagem', 1000, 'API', 'client-1')
    db.mark_sent('client-key-1', 'external-1')
    assert db.client_send_gate('client-1') == (False, 'limite por cliente atingido (1/1 em 24h)')
    assert db.client_send_gate('client-2') == (True, 'ok')


def test_message_gate_counts_only_fallback_messages(tmp_path):
    db = Database(str(tmp_path / 'message-limit.db'))
    db.register_proposal(None, 'proposal-key', 'Proposta', 'mensagem', 1000, 'API', 'client-1')
    db.mark_sent('proposal-key', 'external-proposal')
    assert db.client_message_gate('client-1') == (True, 'ok')

    db.register_proposal(None, 'message-key', 'Pergunta', 'mensagem', None, 'API', 'client-1')
    db.mark_sent('message-key', 'external-message')
    assert db.client_message_gate('client-1') == (False, 'limite de mensagens por cliente atingido (1/1 em 24h)')


def test_reregister_sent_proposal_stays_sent(tmp_path):
    db = Database(str(tmp_path / 'idempotency.db'))
    proposal_id = db.register_proposal(None, 'same-key', 'Proposta', 'primeira', 1000, 'API', 'client-1')
    db.mark_sent('same-key', 'external-1')
    db.register_proposal(None, 'same-key', 'Proposta', 'tentativa repetida', 900, 'API', 'client-1')
    row = db.conn.execute('SELECT id,status,suggested_price FROM proposals WHERE id=?', (proposal_id,)).fetchone()
    assert row == (proposal_id, 'SENT', 900)


def test_send_claim_is_atomic_and_idempotent(tmp_path):
    db = Database(str(tmp_path / 'claim.db'))
    db.register_proposal(None, 'claim-key', 'Proposta', 'mensagem', 1000, 'API', 'client-1')

    assert db.claim_send('claim-key') is True
    assert db.claim_send('claim-key') is False
    assert db.client_send_gate('client-1') == (False, 'limite por cliente atingido (1/1 em 24h)')

    assert db.mark_sent('claim-key', 'external-1') is True
    assert db.mark_sent('claim-key', 'external-2') is False
    assert db.conn.execute(
        'SELECT status,sent_external_id FROM proposals WHERE idempotency_key=?', ('claim-key',)
    ).fetchone() == ('SENT', 'external-1')


def test_runtime_control_kill_switch_blocks_authorized_send():
    control = RuntimeControl(False)
    control.set_kill_switch(True)
    sender = MockSender()

    stats = run_pipeline(
        AuthorizedMockProvider([job()]),
        Database(':memory:'),
        auto_send=True,
        dry_run=False,
        kill_switch=False,
        runtime_control=control,
        sender=sender,
    )

    assert stats['sent'] == 0
    assert not sender.sent


def test_http_provider_reads_json_feed_without_send(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({'jobs': [{'title': 'API', 'description': 'Criar API', 'external_id': '1'}]}).encode()

    monkeypatch.setattr('freelahunter.providers.urlopen', lambda request, timeout: Response())
    provider = HttpJobProvider('https://feed.example/jobs', name='feed')

    jobs = provider.search()

    assert len(jobs) == 1
    assert jobs[0].title == 'API'
    assert jobs[0].platform == 'feed'
    assert provider.capabilities.authorized_send is False


def test_http_provider_rejects_non_https():
    with pytest.raises(ValueError, match='HTTPS'):
        HttpJobProvider('http://feed.example/jobs')

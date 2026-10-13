from freelahunter.core import CircuitBreaker, Config, Database, ProfileService, ProposalValidator, ProposalDraft, MockProvider
from freelahunter.quoting import build_quote
from freelahunter.scheduler import HunterScheduler
from freelahunter.core import ProviderRegistry

LANDING_EN = ('Build a responsive landing page with the approved layout, final copy and images. '
              'The delivery includes HTML and CSS, deployment, and one visual review against acceptance criteria. '
              'The client will provide hosting access, brand assets, final content, and a clear approval contact.')


def test_brl_symbol_is_not_detected_as_usd():
    profile = ProfileService('config/upwork_profile.yaml').load()
    result = build_quote({'budget_text': 'R$ 1.000 - R$ 3.000', 'title': 'Landing page', 'description': LANDING_EN}, profile)
    assert result['currency'] == 'BRL'


def test_euro_quote_passes_validation():
    profile = ProfileService('config/upwork_profile.yaml').load()
    result = build_quote({'budget_text': '€1,000', 'title': 'Landing page', 'description': LANDING_EN}, profile)
    draft = ProposalDraft(0, result['subject'], result['message'], result['estimated_hours'],
                          result['suggested_price'], result['questions'], currency=result['currency'])
    assert ProposalValidator().validate(draft, profile) == ('PASSED', [])


def test_build_quote_does_not_mutate_settings():
    settings = {'hourly_rate': 45, 'minimum_price': 500, 'productive_team_hours_per_day': 6,
                'risk_buffer': .25, 'qa_fraction': .2, 'feedback_business_days': 2, 'fee_fraction': .2}
    build_quote({'title': 'x', 'description': 'y', 'currency': 'USD'}, ProfileService().load(), settings)
    assert 'currency' not in settings


def test_no_response_pause_triggers(tmp_path):
    db = Database(str(tmp_path / 'p.db'))
    for n in range(5):
        db.register_proposal(None, f'k{n}', 'Proposta', 'm', 1000)
        db.mark_sent(f'k{n}', f'e{n}')
    db.conn.execute("UPDATE proposals SET sent_at='2020-01-01T00:00:00+00:00'"); db.conn.commit()
    assert db.evaluate_pause() is True


def test_client_history_normalizes_key(tmp_path):
    db = Database(str(tmp_path / 'h.db'))
    db.register_proposal(None, 'k', 'Proposta', 'm', 1000, client_key='https://www.99freelas.com.br/users/Ana')
    assert db.client_history('https://99freelas.com.br/user/Ana')['known'] is True


def test_config_empty_environ_uses_defaults(monkeypatch):
    monkeypatch.setenv('DRY_RUN', 'false')
    assert Config({}).dry_run is True


def test_scheduler_success_resets_breaker():
    class Flaky(MockProvider):
        name = 'flaky'; calls = 0
        def search(self):
            Flaky.calls += 1
            if Flaky.calls % 2: raise RuntimeError('boom')
            return []
    reg = ProviderRegistry(); reg.register('flaky', Flaky())
    sched = HunterScheduler(reg, Database(':memory:'), Config({}))
    for _ in range(6): sched.run_cycle()
    assert sched.breaker.allow('flaky')

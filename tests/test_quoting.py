import json
import re
import pytest
from freelahunter.core import ProfileService, PriceEstimator, ProposalDraft, ProposalValidator
from freelahunter.quoting import build_quote, ROOT


def quote(title, description='', **kwargs):
    return build_quote(dict(title=title, description=description, **kwargs), ProfileService().load())


def test_vague_scope_gets_question_without_binding_amount():
    result = quote('Automação Python', 'Quero automatizar meu negócio.')
    assert result['action'] == 'question'
    assert result['suggested_price'] is None
    assert result['estimated_days'] is None
    assert result['price_range'][0] > 700
    assert 'podemos combinar um valor justo' in result['question']
    assert 'equipe de dois desenvolvedores full stack' in result['question']
    assert 'que trabalham juntos' not in result['question']


def test_complex_scope_takes_more_time_than_landing_page():
    simple = quote('Landing page')
    complex_job = quote('Marketplace SaaS com login, pagamentos, dashboard e integração API')
    assert complex_job['hours_range'][0] > simple['hours_range'][1]
    assert complex_job['days_range'][1] > simple['days_range'][1]
    assert complex_job['action'] == 'question'


def test_low_budget_does_not_lower_actual_estimate():
    initial = quote('API Python', budget_max=100000)
    cheap = quote('API Python', budget_max=100)
    assert cheap['price_range'] == initial['price_range']
    assert any('orçamento' in q for q in cheap['questions'])
    assert PriceEstimator(hourly_rate=100, minimum_project_price=500).estimate(20, budget_max=100)[0] == 2000


def test_fee_is_fraction_of_final_amount():
    result = quote('Landing page')
    assert result['client_total_range'][0] == result['price_range'][0] / .8


def test_proposal_has_no_fabricated_experience_and_has_negotiation():
    description = ('Uma landing page responsiva com apresentação, serviços e contato. '
                   'Fornecemos layout aprovado, textos, imagens e hospedagem. '
                   'Entregar arquivos HTML e CSS, formulário apenas visual e botões com links. '
                   'Aceite por comparação do layout em celular e desktop, com uma revisão.')
    result = quote('Landing page', description)
    assert result['action'] == 'proposal'
    assert result['estimated_days'] > 1
    assert 'preço pode ser negociado' in result['message']
    assert 'equipe de dois desenvolvedores full stack' in result['message']
    assert 'que trabalham juntos' not in result['message']
    assert f"R$ {result['suggested_price']:,.2f}".replace(',', '_').replace('.', ',').replace('_', '.') in result['message']
    assert f"prazo de até {result['estimated_days']} dias" in result['message']
    assert 'contas do cliente' in result['message']
    assert 'experiência comprovável' not in result['message']
    assert 'estudante' not in result['message']
    assert 'etapas verificáveis' in result['message']
    assert 'aqui pela plataforma' in result['message']
    assert result['knowledge']['advisory_only'] is True


def test_client_name_personalizes_greeting():
    result = quote('Landing page',
                   'Landing page responsiva. Layout aprovado, textos e imagens fornecidos. '
                   'Formulário visual, HTML e CSS, aceite em desktop e celular com uma revisão.',
                   client='Guilherme H.')
    assert result['message'].startswith('Olá, Guilherme H.!')


def test_market_reference_is_advisory_and_does_not_override_calculation():
    settings = json.loads((ROOT / 'config/pricing.json').read_text())
    settings['hourly_rate'] = 30
    settings.pop('proposal_price_floor', None)
    settings.pop('proposal_price_ceiling', None)
    result = build_quote({'title': 'Landing page', 'description':
        'Landing page responsiva com layout aprovado, textos, imagens e formulário visual. '
        'Entregar HTML e CSS com aceite em desktop e celular e uma rodada de revisão.'},
        ProfileService().load(), settings)
    assert result['price_range'][1] == result['hours_range'][1] * 30
    assert any('abaixo da referência consultiva' in note for note in result['knowledge']['notes'])


def test_configured_proposal_price_band_limits_values():
    result = quote(
        'Marketplace SaaS com pagamentos',
        'Criar uma plataforma com API, painel, login e pagamento. '
        'O cliente fornece layout aprovado, textos, imagens, regras de negócio e acessos. '
        'A entrega inclui implementação, testes, documentação e validação dos fluxos principais '
        'em desktop e celular com uma rodada de revisão.',
    )
    assert all(900 <= price <= 6000 for price in result['price_range'])
    assert result['action'] == 'question'
    assert result['suggested_price'] is None


def test_generated_message_passes_validator_with_explicit_price_and_commercial_terms():
    result = quote(
        'Landing page',
        'Criar uma landing page responsiva com apresentação dos serviços, depoimentos e contato. '
        'Fornecemos layout aprovado, textos e imagens finais. A entrega inclui HTML e CSS, '
        'publicação na hospedagem do cliente e uma rodada de revisão com comparação visual em desktop e celular.',
    )
    proposal = ProposalDraft(
        1, result['subject'], result['message'], result['estimated_hours'],
        result['suggested_price'], result['questions'],
    )

    status, reasons = ProposalValidator().validate(proposal, ProfileService().load())

    assert status == 'PASSED', reasons
    assert 'equipe de dois desenvolvedores full stack' in result['message']
    assert 'preço pode ser negociado' in result['message']
    assert 'contas do cliente' in result['message']


@pytest.mark.parametrize(
    ('setting', 'value'),
    [
        ('proposal_price_floor', 0),
        ('proposal_price_ceiling', 0),
        ('proposal_price_ceiling', 899),
        ('fee_fraction', 1),
        ('risk_buffer', -0.01),
    ],
)
def test_invalid_price_configuration_is_rejected(setting, value):
    settings = json.loads((ROOT / 'config/pricing.json').read_text())
    settings[setting] = value

    with pytest.raises(ValueError):
        build_quote({'title': 'Landing page', 'description': 'Criar uma landing page.'}, ProfileService().load(), settings)


def test_validator_rejects_proposal_message_without_price():
    result = quote(
        'Landing page',
        'Criar uma landing page responsiva com apresentação dos serviços, depoimentos e contato. '
        'Fornecemos layout aprovado, textos e imagens finais. A entrega inclui HTML e CSS, '
        'publicação na hospedagem do cliente e uma rodada de revisão com comparação visual em desktop e celular.',
    )
    message_without_price = re.sub(r'R\$ [\d.]+,\d{2}', '', result['message'])
    proposal = ProposalDraft(1, result['subject'], message_without_price, 10, result['suggested_price'], [])

    status, reasons = ProposalValidator().validate(proposal, ProfileService().load())

    assert status == 'FAILED', reasons


def test_validator_rejects_message_with_inconsistent_price():
    result = quote(
        'Landing page',
        'Criar uma landing page responsiva com apresentação dos serviços, depoimentos e contato. '
        'Fornecemos layout aprovado, textos e imagens finais. A entrega inclui HTML e CSS, '
        'publicação na hospedagem do cliente e uma rodada de revisão com comparação visual em desktop e celular.',
    )
    proposal = ProposalDraft(1, result['subject'], result['message'], 10, 900, [])

    status, reasons = ProposalValidator().validate(proposal, ProfileService().load())

    assert status == 'FAILED', reasons


def test_off_platform_contact_requires_manual_review():
    result = quote('Site institucional', 'Criar site institucional. Chama no WhatsApp para explicar o projeto.')
    assert result['action'] == 'skip'
    assert 'fora da plataforma' in result['reason']


@pytest.mark.parametrize('extra', [{'has_gold_badge': True}, {'is_premium': True}])
def test_restricted_jobs_skipped(extra):
    assert quote('API Python', **extra)['action'] == 'skip'


def test_unknown_work_cannot_get_invented_quote():
    result = quote('Preciso de ajuda', 'Explico depois')
    assert result['price_range'] is None
    assert result['action'] == 'question'


def test_upwork_profile_generates_english_pair_copy_in_usd():
    profile = ProfileService('config/upwork_profile.yaml').load()
    result = build_quote({
        'platform': 'upwork',
        'currency': 'USD',
        'title': 'Responsive landing page',
        'description': 'Build a responsive landing page with the approved layout, final copy and images. '
                       'The delivery includes HTML and CSS, deployment to the client hosting, and one visual review '
                       'against desktop and mobile acceptance criteria. The client will provide hosting access, '
                       'brand assets, final content, and a clear approval contact for the milestone.',
    }, profile)

    assert result['action'] == 'proposal'
    assert result['currency'] == 'USD'
    assert result['subject'].startswith('Proposal:')
    assert 'two-developer full-stack team' in result['message']
    assert 'the price is negotiable' in result['message'].lower()
    assert '$' in result['message']
    assert 'R$' not in result['message']


def test_upwork_quote_uses_detected_euro_currency_override():
    profile = ProfileService('config/upwork_profile.yaml').load()
    result = build_quote({
        'platform': 'upwork',
        'budget_text': '€1,000–€3,000',
        'title': 'Responsive landing page',
        'description': 'Build a responsive landing page with the approved layout, final copy and images. '
                       'The delivery includes HTML and CSS, deployment, and one visual review against acceptance criteria. '
                       'The client will provide hosting access, brand assets, final content, and a clear approval contact.',
    }, profile)

    assert result['currency'] == 'EUR'
    assert '€' in result['message']


def test_plural_landing_pages_require_quantity_and_assets():
    result = quote('Desenvolvimento de landing pages',
                   'Criar landing pages profissionais para três nichos diferentes, com SEO e CTA.')
    assert result['action'] == 'question'
    assert result['suggested_price'] is None
    assert any('Quantas landing pages' in question for question in result['questions'])


def test_payment_project_requires_gateway_and_business_rules():
    result = quote('Central de pagamentos',
                   'Receber pagamentos por Pix, boleto e cartões de crédito e débito.')
    assert result['action'] == 'question'
    assert any('gateway' in question for question in result['questions'])


def test_wordpress_site_does_not_claim_missing_specialty():
    result = quote('Criação de site WordPress',
                   'Criar um site profissional responsivo; páginas e materiais serão alinhados antes do início.')
    assert not any('experiência necessária' in question for question in result['questions'])


def test_invalid_configuration_rejected():
    settings = json.loads((ROOT / 'config/pricing.json').read_text())
    settings['productive_team_hours_per_day'] = 0
    with pytest.raises(ValueError):
        build_quote({'title': 'API'}, ProfileService().load(), settings)


def test_validator_requires_price_negotiation_message():
    from freelahunter.core import Profile, ProposalDraft, ProposalValidator
    proposal = ProposalDraft(1, 'Proposta', 'palavra ' * 110, 10, 1000, [])
    status, reasons = ProposalValidator().validate(proposal, Profile())
    assert status == 'FAILED'
    assert 'proposta não informa que o valor pode ser negociado' in reasons


def test_validator_requires_two_developer_team_message():
    from freelahunter.core import Profile, ProposalDraft, ProposalValidator
    message = ('Podemos negociar o valor conforme o escopo. ' + 'palavra ' * 105)
    proposal = ProposalDraft(1, 'Proposta', message, 10, 1000, [])
    status, reasons = ProposalValidator().validate(proposal, Profile())
    assert status == 'FAILED'
    assert 'proposta não apresenta a equipe de dois desenvolvedores full stack' in reasons


def test_pipeline_does_not_send_unresolved_scope():
    from freelahunter.core import AuthorizedMockProvider, Database, Job, MockSender, run_pipeline
    sender = MockSender()
    result = run_pipeline(AuthorizedMockProvider([Job('API Python', 'Integrar sistemas',
        external_id='unknown', budget_max=20000, skills=['Python', 'FastAPI', 'SQL'])]),
        Database(':memory:'), sender=sender, auto_send=True, dry_run=False, kill_switch=False)
    assert result['review'] == 1
    assert result['sent'] == 0
    assert not sender.sent


def test_reais_in_budget_text_do_not_become_dollars():
    from freelahunter.quoting import detect_currency

    assert detect_currency({'budget_text': 'R$ 1.500,00'}, ProfileService().load(), {}) == 'BRL'


def test_portuguese_workana_quote_keeps_foreign_currency():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, 'scripts/generate_proposal.py'], input=json.dumps({
        'platform': 'workana', 'currency': 'USD', 'title': 'Landing page',
        'description': 'Landing page responsiva com layout aprovado, textos e imagens fornecidos. HTML e CSS com aceite em celular e desktop e uma revisão.',
    }), capture_output=True, text=True, encoding='utf-8', check=True)
    draft = json.loads(result.stdout)
    assert 'R$' not in draft['fallback_message']
    assert '$' in draft['fallback_message']
    assert 'Olá' in draft['fallback_message']

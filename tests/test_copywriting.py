import re

import pytest

from freelahunter import copywriting
from freelahunter.core import ProfileService, ProposalDraft, ProposalValidator
from freelahunter.quoting import build_quote

LANDING = ('Criar uma landing page responsiva com apresentação dos serviços, depoimentos e contato. '
           'Fornecemos layout aprovado, textos e imagens finais. A entrega inclui HTML e CSS, '
           'publicação na hospedagem do cliente e uma rodada de revisão com comparação visual em desktop e celular.')
LANDING_EN = ('Build a responsive landing page with the approved layout, final copy and images. '
              'The delivery includes HTML and CSS, deployment to the client hosting, and one visual review '
              'against desktop and mobile acceptance criteria. The client will provide hosting access, '
              'brand assets, final content, and a clear approval contact for the milestone.')


def english_profile():
    profile = ProfileService('config/upwork_profile.yaml').load()
    profile.locale, profile.currency, profile.pricing_path = 'en-US', 'USD', 'config/upwork_pricing.json'
    return profile


@pytest.mark.parametrize('style', copywriting.STYLES)
@pytest.mark.parametrize('client', ['', 'Maria Eduarda Albuquerque S.'])
def test_every_style_passes_validator_in_portuguese(style, client):
    profile = ProfileService().load()
    result = build_quote({'title': 'Landing page', 'description': LANDING, 'client': client,
                          'proposal_style': style}, profile)
    draft = ProposalDraft(0, result['subject'], result['message'], 0, result['suggested_price'], [],
                          currency=result['currency'])
    assert result['template_variant'] == style
    assert ProposalValidator().validate(draft, profile) == ('PASSED', [])


@pytest.mark.parametrize('style', copywriting.STYLES)
def test_every_style_passes_validator_in_english(style):
    profile = english_profile()
    result = build_quote({'platform': 'upwork', 'currency': 'USD', 'title': 'Responsive landing page',
                          'description': LANDING_EN, 'proposal_style': style}, profile)
    draft = ProposalDraft(0, result['subject'], result['message'], 0, result['suggested_price'], [],
                          currency=result['currency'])
    assert ProposalValidator().validate(draft, profile) == ('PASSED', [])
    assert 'here on Upwork' in result['message']
    assert result['message'].startswith('Hi!')


def test_message_opens_with_client_problem_and_keeps_duplicate_guard_markers():
    result = build_quote({'title': 'Landing page', 'description': LANDING, 'client': 'Ana'}, ProfileService().load())
    first_paragraph = result['message'].split('\n\n')[0]
    assert first_paragraph.startswith('Olá, Ana!')
    assert '“Landing page”' in first_paragraph
    assert 'landing page responsiva' in first_paragraph
    # submission_guard.hasPriorProjectIntroduction relies on both markers.
    for text in (result['message'], result['question'], result['fallback_message']):
        assert 'dois desenvolvedores full stack' in text
        assert 'landing page' in text.lower()


def test_same_job_always_gets_same_copy_and_styles_are_spread():
    profile = ProfileService().load()
    job = {'title': 'Landing page', 'description': LANDING, 'url': 'https://www.99freelas.com.br/project/x-1'}
    assert build_quote(job, profile)['message'] == build_quote(dict(job), profile)['message']
    styles = {copywriting.choose_style(f'https://www.99freelas.com.br/project/job-{n}') for n in range(60)}
    assert styles == set(copywriting.STYLES)


def test_style_selection_exploits_best_response_rate_after_enough_data():
    stats = {'A': {'sent': 20, 'responded': 2}, 'B': {'sent': 20, 'responded': 9}, 'C': {'sent': 20, 'responded': 3}}
    picks = [copywriting.choose_style(f'job-{n}', stats) for n in range(200)]
    assert picks.count('B') > 140
    assert {'A', 'C'} & set(picks)  # exploration continues
    few = {'A': {'sent': 3, 'responded': 3}, 'B': {'sent': 20, 'responded': 0}, 'C': {'sent': 20, 'responded': 0}}
    assert {copywriting.choose_style(f'job-{n}', few) for n in range(60)} == set(copywriting.STYLES)
    assert copywriting.choose_style('job-1', stats, forced='c') == 'C'


def test_client_message_has_at_most_three_questions_and_no_internal_notes():
    result = build_quote({'title': 'App Android com pagamentos e integração',
                          'description': 'Aplicativo Android com login, pagamento por Pix, integração com ERP via API, '
                                         'migração do sistema legado e 5 telas. Plataforma completa.',
                          'budget_max': 100, 'deadline_days': 2}, ProfileService().load())
    assert len(result['questions']) > 3  # full list stays available for review
    assert len(result['client_questions']) == 3
    assert len(re.findall(r'\(\d\)', result['question'])) == 3
    assert 'experiência necessária' not in result['question']
    assert 'gateway' in result['question']  # payment outranks budget/deadline


def test_direct_message_is_available_for_forced_proposals():
    result = build_quote({'title': 'API Python', 'description': 'Criar API REST com FastAPI.'}, ProfileService().load())
    assert result['action'] == 'question'
    assert result['suggested_price'] is None
    assert 'R$ ' in result['direct_message']
    assert 'preço pode ser negociado' in result['direct_message']


def test_technologies_cited_only_when_in_job_and_profile():
    profile = ProfileService().load()
    result = build_quote({'title': 'Site WordPress em Django', 'description': LANDING + ' Usar Python.'}, profile)
    techs = result['message'].split('\n\n')[0].split('usando ')[1]
    assert 'Python' in techs
    assert 'Django' not in techs and 'WordPress' not in techs


def test_portfolio_is_cited_only_when_opted_in_and_relevant():
    portfolio = [{'title': 'Bot interno', 'technologies': ['Python', 'FastAPI']},
                 {'title': 'Portal de pedidos', 'cite': True, 'keywords': ['erp', 'pedidos'],
                  'technologies': ['FastAPI'], 'url': 'https://example.com/case'}]
    text = 'api fastapi que recebe pedidos e integra com erp'
    sentence = copywriting.portfolio_sentence(portfolio, text, english=False)
    assert 'Portal de pedidos' in sentence and 'https://example.com/case' in sentence
    assert copywriting.portfolio_sentence(portfolio, 'landing page html', english=False) == ''
    assert copywriting.portfolio_sentence(portfolio[:1], text, english=False) == ''

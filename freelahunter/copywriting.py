"""Deterministic proposal copy: problem-first, varied and measurable.

There is no language model here.  Every sentence comes from a template, and
the choice between templates is a pure function of the job (stable hash) plus,
optionally, the observed response rate of each style.  The same job therefore
always produces the same text, which keeps idempotency keys, the analysis
cache and the duplicate-contact guards stable.

Invariants the rest of the system depends on:
- the message mentions the project title and the team description
  (``hasPriorProjectIntroduction`` and ``ProposalValidator``);
- price, deadline and negotiation language come verbatim from the quote;
- paid services stay in the client's accounts.
"""
from __future__ import annotations

import hashlib
import re

STYLES = ('A', 'B', 'C')
MAX_CLIENT_QUESTIONS = 3

# Lower number = asked first.  Questions not listed keep priority 50.
QUESTION_PRIORITY = {
    'scope': 10, 'payment': 20, 'integration': 25, 'legacy': 30, 'mvp': 35,
    'platform': 40, 'landing_pages': 40, 'site_pages': 40, 'excluded_parts': 45,
    'volumes': 50, 'budget': 60, 'deadline': 65,
}
# Notes for the operator, never sent to the client.
INTERNAL_QUESTIONS = {'specialty'}

PT = {
    'hook': {
        'A': 'Lemos a descrição de “{title}” e o ponto central é {focus}.',
        'B': 'Pelo que vimos em “{title}”, a necessidade é {focus}, e esse é exatamente o tipo de entrega que fazemos.',
        'C': 'Sobre “{title}”: entendemos que o objetivo é {focus}.',
    },
    'team': {
        'A': 'Somos uma {team} que trabalha em conjunto, do alinhamento à entrega.',
        'B': 'Quem atende é uma {team}: trabalhamos juntos em todas as etapas, então o projeto não depende de uma pessoa só.',
        'C': 'Somos uma {team} e cuidamos juntos de backend, frontend e testes.',
    },
    'process': {
        'A': 'Propomos executar em etapas verificáveis: alinhamento dos requisitos, implementação, testes e entrega documentada.',
        'B': 'Trabalhamos em etapas verificáveis, com entregas parciais para vocês acompanharem e aprovarem antes de seguir.',
        'C': 'Dividimos o trabalho em etapas verificáveis (alinhamento, implementação, testes e entrega documentada) para vocês validarem cada parte.',
    },
    'terms': ('Combinamos os critérios de aceite antes de iniciar, e o prazo começa após recebermos acessos, '
              'materiais e a confirmação do escopo. Novas funcionalidades são orçadas separadamente.'),
    'paid': 'APIs, hospedagem, domínio e serviços pagos ficam nas contas do cliente.',
    'price': ('Como proposta inicial para esse escopo, indicamos {price} e prazo de até {days} dias corridos, '
              'incluindo testes e uma rodada de revisão.'),
    'negotiation': {
        'A': 'Esse é um valor de referência: o preço pode ser negociado conforme os detalhes finais e as prioridades do projeto.',
        'B': 'O preço pode ser negociado conforme o escopo final; se fizer sentido, começamos por uma primeira etapa menor.',
        'C': 'Esse é o ponto de partida, e o preço pode ser negociado depois de confirmarmos os detalhes e as prioridades.',
    },
    'close': {
        'A': 'Se fizer sentido, alinhamos as etapas e ajustamos a proposta aqui pela plataforma.',
        'B': 'Ficamos à disposição para tirar dúvidas e ajustar a proposta aqui pela plataforma.',
        'C': 'Podemos detalhar o plano de entregas aqui pela plataforma assim que preferirem.',
    },
    'portfolio': 'Um trabalho relacionado nosso: {name}{summary}.',
    'ask': 'Para fechar um valor justo, precisamos confirmar:',
    'ask_short': 'Antes de fechar, queremos confirmar:',
    'question_close': ('Depois desses detalhes, o valor é a combinar conforme o escopo e as prioridades; '
                       'podemos combinar um valor justo.'),
    'fallback_scope': 'Pelo que foi descrito, o escopo inicial inclui {scope}, com testes e entrega documentada.',
    'fallback_price': ('Nossa estimativa preliminar é de {price}, com prazo de até {days} dias corridos. '
                       'O preço pode ser negociado conforme os detalhes finais, as prioridades e os critérios de aceite.'),
    'fallback_no_price': ('Queremos entender melhor as funcionalidades, entradas, saídas e critérios de aceite antes de '
                          'fechar o escopo. O preço pode ser negociado depois dessa confirmação.'),
    'default_scope': 'a análise do serviço e a implementação',
    'default_focus': 'um projeto com escopo claro e entregas verificáveis',
    'and': 'e',
    'plus': '{first}, além de {second}',
    'using': 'usando {techs}',
}

EN = {
    'hook': {
        'A': 'We read the description of "{title}", and the core of it is {focus}.',
        'B': 'From "{title}", the need is {focus}, which is exactly the kind of delivery we do.',
        'C': 'About "{title}": we understand the goal is {focus}.',
    },
    'team': {
        'A': 'We are a {team} working together from scoping to handoff.',
        'B': 'You would work with a {team}, so the project never depends on a single person.',
        'C': 'We are a {team} covering backend, frontend, and testing together.',
    },
    'process': {
        'A': 'We propose working in verifiable milestones covering requirements, implementation, testing, and documented handoff.',
        'B': 'We work in verifiable milestones with partial deliveries you can review and approve before we move on.',
        'C': 'We split the work into verifiable milestones (requirements, implementation, testing, and documented handoff) so you can validate each part.',
    },
    'terms': ('We agree on acceptance criteria before starting, and the timeline begins once access, materials, '
              'and scope confirmation are available. New functionality is estimated separately.'),
    'paid': 'Paid APIs, hosting, domains, and services remain in the client account.',
    'price': ('As an initial proposal for this scope, we estimate {price} and up to {days} calendar days, '
              'including testing and one revision round.'),
    'negotiation': {
        'A': 'This is an initial estimate; the price is negotiable based on the final details, scope, and priorities.',
        'B': 'The price is negotiable once the final scope is confirmed, and we can start with a smaller first milestone.',
        'C': 'This is a starting point, and the price is negotiable after we confirm the details and priorities.',
    },
    'close': {
        'A': 'We can align the milestones and adjust the proposal here on {platform}.',
        'B': 'Happy to answer questions and adjust the proposal here on {platform}.',
        'C': 'We can detail the delivery plan here on {platform} whenever you prefer.',
    },
    'portfolio': 'A related project of ours: {name}{summary}.',
    'ask': 'To agree on a fair price, we need to confirm:',
    'ask_short': 'Before finalizing, we would like to confirm:',
    'question_close': ('Once these details are confirmed, we can agree on a fair price based on the scope and priorities. '
                       'The price is negotiable.'),
    'fallback_scope': 'Based on the description, the initial scope covers {scope}, with testing and documented handoff.',
    'fallback_price': ('Our preliminary estimate is {price}, with up to {days} calendar days. '
                       'The price is negotiable after confirming the final details, priorities, and acceptance criteria.'),
    'fallback_no_price': ('We would like to understand the functionality, inputs, outputs, and acceptance criteria '
                          'before finalizing the scope. The price is negotiable after confirmation.'),
    'default_scope': 'service analysis and implementation',
    'default_focus': 'a project with a clear scope and verifiable milestones',
    'and': 'and',
    'plus': '{first}, plus {second}',
    'using': 'using {techs}',
}

# One sentence of domain care per deliverable.  Keys are the internal
# Portuguese deliverable names from quoting.RULES.
EXPERTISE = {
    'landing page': (
        'Em landing page, priorizamos responsividade, velocidade de carregamento e formulário ou CTA funcionando antes da publicação.',
        'For landing pages, we take care of responsiveness, load speed, and a working form or CTA before publishing.'),
    'site institucional': (
        'Em site institucional, priorizamos páginas bem estruturadas, responsividade e SEO básico (títulos, metadados e desempenho).',
        'For business websites, we prioritize well-structured pages, responsiveness, and basic SEO (titles, metadata, and performance).'),
    'site em CMS ou blog': (
        'Em CMS, deixamos o painel organizado para vocês editarem o conteúdo sem depender de desenvolvedor.',
        'With a CMS, we leave the admin organized so you can edit content without depending on a developer.'),
    'API e persistência': (
        'Na API, entregamos endpoints documentados, validação de entrada e tratamento de erros, para a integração não falhar em silêncio.',
        'On the API side, we deliver documented endpoints, input validation, and error handling so integrations never fail silently.'),
    'interface web': (
        'Na interface, componentizamos as telas e testamos os fluxos principais em desktop e celular.',
        'On the interface, we build reusable components and test the main flows on desktop and mobile.'),
    'automação de fluxo': (
        'Na automação, registramos logs de cada execução e tratamos falhas, para nada se perder sem aviso.',
        'For automations, we log every run and handle failures so nothing gets lost without notice.'),
    'autenticação e permissões': (
        'Em login e permissões, separamos os perfis de acesso e protegemos as rotas no backend, não só na tela.',
        'For login and permissions, we separate access roles and protect routes on the backend, not only in the UI.'),
    'integrações externas': (
        'Nas integrações, tratamos limites de requisição, nova tentativa em caso de falha e registro do que foi sincronizado.',
        'For integrations, we handle rate limits, retries on failure, and a record of what was synchronized.'),
    'pagamentos e conciliação': (
        'Em pagamentos, tratamos os webhooks de confirmação e a conciliação, para nenhuma cobrança ficar sem baixa.',
        'For payments, we handle confirmation webhooks and reconciliation so no charge is left unaccounted for.'),
    'agenda e reservas': (
        'Em agendamento, cuidamos de conflitos de horário, fuso e confirmações.',
        'For scheduling, we handle time conflicts, time zones, and confirmations.'),
    'relatórios e painel': (
        'No painel, definimos os indicadores com vocês antes, para os números baterem com a operação.',
        'For dashboards, we define the metrics with you first so the numbers match your operation.'),
    'migração e refatoração': (
        'Em código existente, primeiro reproduzimos o problema e mapeamos o que já funciona, para não quebrar nada no caminho.',
        'With an existing codebase, we first reproduce the issue and map what already works so nothing breaks along the way.'),
    'arquitetura multiempresa': (
        'Em SaaS ou marketplace, isolamos os dados de cada cliente desde o início.',
        'For SaaS or marketplaces, we isolate each customer\'s data from day one.'),
}

FOCUS = {
    'landing page': ('uma landing page responsiva e pronta para converter', 'a responsive landing page ready to convert'),
    'site institucional': ('um site profissional e responsivo', 'a professional, responsive website'),
    'site em CMS ou blog': ('um site em CMS fácil de manter', 'an easy-to-maintain CMS website'),
    'API e persistência': ('uma API confiável com dados bem estruturados', 'a reliable API with well-structured data'),
    'interface web': ('uma interface web clara e funcional', 'a clear, functional web interface'),
    'automação de fluxo': ('uma automação do fluxo com controle de cada execução', 'a workflow automation with visibility into every run'),
    'autenticação e permissões': ('login e permissões bem resolvidos', 'solid login and permissions'),
    'integrações externas': ('a integração segura entre os sistemas', 'a safe integration between the systems'),
    'pagamentos e conciliação': ('o recebimento de pagamentos com conciliação confiável', 'payment collection with reliable reconciliation'),
    'agenda e reservas': ('um agendamento sem conflitos', 'conflict-free scheduling'),
    'relatórios e painel': ('um painel com indicadores confiáveis', 'a dashboard with trustworthy metrics'),
    'migração e refatoração': ('a evolução do código atual sem quebrar o que já funciona', 'an upgrade of the current code without breaking what works'),
    'arquitetura multiempresa': ('uma base SaaS preparada para vários clientes', 'a SaaS foundation ready for multiple customers'),
    'aplicativo mobile': ('um aplicativo mobile', 'a mobile app'),
    'avaliação de IA e dados': ('a aplicação de IA e dados ao processo', 'AI and data applied to the process'),
}


def stable_unit(*parts) -> float:
    """Map arbitrary text to a stable number in [0, 1)."""
    digest = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12)


def choose_style(seed: str, stats: dict | None = None, forced: str | None = None,
                 min_samples: int = 15, explore: float = 0.25) -> str:
    """Pick a copy style.

    Without enough data every style gets a third of the jobs (stable per job).
    Once every style has ``min_samples`` sends, the best response rate wins
    most jobs while ``explore`` of them keep testing the others.
    """
    forced = str(forced or '').strip().upper()
    if forced in STYLES:
        return forced
    unit = stable_unit('style', seed)
    uniform = STYLES[int(unit * len(STYLES)) % len(STYLES)]
    if not stats or any(stats.get(s, {}).get('sent', 0) < min_samples for s in STYLES):
        return uniform
    if stable_unit('explore', seed) < explore:
        return uniform
    return max(STYLES, key=lambda s: (stats[s].get('responded', 0) / stats[s]['sent'], -STYLES.index(s)))


def prioritized_questions(tagged: list[tuple[str, str]], limit: int = MAX_CLIENT_QUESTIONS) -> list[str]:
    """Client-facing questions: internal notes removed, most important first."""
    ranked = sorted(((QUESTION_PRIORITY.get(tag, 50), index, text) for index, (tag, text) in enumerate(tagged)
                     if tag not in INTERNAL_QUESTIONS))
    return [text for _, _, text in ranked[:limit]]


def join_list(items: list[str], conjunction: str) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return ''.join(items)
    if any(f' {conjunction} ' in item or ',' in item for item in items):
        return '; '.join(items)  # labels that already contain "e"/commas
    return ', '.join(items[:-1]) + f' {conjunction} ' + items[-1]


def focus_phrase(task_names: list[str], technologies: list[str], english: bool) -> str:
    lang = EN if english else PT
    index = 1 if english else 0
    phrases = [FOCUS[name][index] for name in task_names if name in FOCUS][:2]
    if not phrases:
        return lang['default_focus']
    phrase = phrases[0] if len(phrases) == 1 else lang['plus'].format(first=phrases[0], second=phrases[1])
    techs = [t for t in technologies if t][:3]
    if techs:
        phrase += ' ' + lang['using'].format(techs=join_list(techs, lang['and']))
    return phrase


def expertise_sentence(task_names: list[str], english: bool) -> str:
    for name in task_names:
        if name in EXPERTISE:
            return EXPERTISE[name][1 if english else 0]
    return ''


def portfolio_sentence(portfolio, text: str, english: bool, minimum_overlap: int = 2) -> str:
    """Cite one real portfolio item, only when the profile opts it in.

    An item is eligible only with ``cite: true``; matching counts how many of
    its ``keywords``/``technologies`` appear in the job text.
    """
    best, best_score = None, 0
    normalized_text = text.lower()
    for item in portfolio or []:
        if not isinstance(item, dict) or item.get('cite') is not True:
            continue
        terms = {str(t).lower() for t in (item.get('keywords') or []) + (item.get('technologies') or []) if str(t).strip()}
        score = sum(1 for term in terms if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', normalized_text))
        if score > best_score:
            best, best_score = item, score
    if not best or best_score < minimum_overlap:
        return ''
    name = ' '.join(str(best.get('title') or best.get('name') or '').split())[:80]
    if not name:
        return ''
    url = str(best.get('url') or best.get('demo_url') or '').strip()
    summary = f' ({url})' if url.startswith('https://') else ''
    return (EN if english else PT)['portfolio'].format(name=name, summary=summary)


def compose(*, english: bool, style: str, greeting: str, title: str, team: str, task_names: list[str],
            technologies: list[str], scope: str, questions: list[str], price_text: str | None,
            days: int | None, platform_label: str, portfolio_line: str = '') -> dict:
    """Build the proposal, question and fallback texts for one job."""
    lang = EN if english else PT
    style = style if style in STYLES else 'A'
    hook = lang['hook'][style].format(title=title, focus=focus_phrase(task_names, technologies, english))
    team_line = lang['team'][style].format(team=team)
    expertise = expertise_sentence(task_names, english)
    opening = f'{greeting} {hook}'
    numbered = ' '.join(f'({i}) {q}' for i, q in enumerate(questions, 1))

    def paragraphs(*blocks):
        return '\n\n'.join(' '.join(p for p in block if p) for block in blocks if any(block))

    body = [team_line, expertise, portfolio_line]
    process = [lang['process'][style], lang['terms']]
    close = lang['close'][style].format(platform=platform_label)

    direct = None
    if price_text and days:
        direct = paragraphs(
            [opening], body, process,
            [lang['price'].format(price=price_text, days=days), lang['negotiation'][style], lang['paid']],
            [close],
        )
    question = paragraphs(
        [opening], [team_line, expertise],
        [lang['ask'], numbered] if numbered else [],
        [lang['question_close'], lang['paid']],
    )
    if price_text and days:
        fallback = paragraphs(
            [opening], [team_line, lang['fallback_scope'].format(scope=scope or lang['default_scope'])],
            [lang['fallback_price'].format(price=price_text, days=days)],
            [lang['ask_short'], ' '.join(f'({i}) {q}' for i, q in enumerate(questions[:2], 1))] if questions else [],
            [lang['process'][style], lang['paid']],
        )
    else:
        fallback = paragraphs([opening], [team_line], [lang['fallback_no_price']], [lang['process'][style], lang['paid']])
    return {'direct': direct, 'question': question, 'fallback': fallback, 'style': style}

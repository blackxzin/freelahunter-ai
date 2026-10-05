"""Explainable preliminary estimates, not a market-price oracle."""
import json
import math
import re
import unicodedata
from pathlib import Path

from .knowledge import commercial_advisories

ROOT = Path(__file__).resolve().parents[1]


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.lower())
                   if not unicodedata.combining(c))


def format_brl(value):
    return f'R$ {value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def format_usd(value):
    return f'${value:,.2f}'


def format_money(value, currency='BRL'):
    code = str(currency or 'BRL').upper()
    if code == 'USD':
        return format_usd(value)
    if code == 'EUR':
        return f'€{value:,.2f}'
    if code == 'GBP':
        return f'£{value:,.2f}'
    return format_brl(value)


def detect_currency(snapshot, profile, settings):
    explicit = str(snapshot.get('currency') or '').upper().strip()
    if explicit in {'USD', 'EUR', 'GBP', 'BRL'}:
        return explicit
    text = str(snapshot.get('budget_text') or '') + ' ' + str(snapshot.get('description') or '')
    if re.search(r'€|\bEUR\b', text, re.IGNORECASE):
        return 'EUR'
    if re.search(r'£|\bGBP\b', text, re.IGNORECASE):
        return 'GBP'
    if re.search(r'US\$|\$|\bUSD\b', text, re.IGNORECASE):
        return 'USD'
    if re.search(r'R\$|\bBRL\b', text, re.IGNORECASE):
        return 'BRL'
    return str(settings.get('currency') or getattr(profile, 'currency', 'BRL')).upper()


# Person-hours for bounded deliverables; rate is an internal assumption.
RULES = [
    ('landing page', r'landing page|pagina unica|single page', 12, 24),
    ('site institucional', r'site institucional|site simples|website|business site|corporate site', 20, 40),
    ('site em CMS ou blog', r'wordpress|\bblog\b|cms', 20, 40),
    ('API e persistência', r'\bapi\b|fastapi|backend|back-end|database|persistence', 24, 48),
    ('interface web', r'frontend|front-end|\breact\b|next\.?js|web app|user interface', 24, 48),
    ('automação de fluxo', r'automacao|automation|workflow|\bn8n\b|\bbot\b', 16, 32),
    ('autenticação e permissões', r'login|auth|authentication|autenticacao|permissions|permissoes', 12, 24),
    ('integrações externas', r'integrac|integration|webhook|third-party', 16, 40),
    ('pagamentos e conciliação', r'pagamento|payment|checkout|\bpix\b|stripe|billing', 20, 48),
    ('agenda e reservas', r'agendamento|booking|reservas|reservation|agenda online|scheduling', 20, 40),
    ('relatórios e painel', r'relatorio|report|dashboard|painel administrativo|admin panel', 16, 32),
    ('migração e refatoração', r'migracao|migration|refator|legacy|codebase', 24, 64),
    ('arquitetura multiempresa', r'\bsaas\b|marketplace|multi.?tenant|multi-tenant', 40, 80),
    ('aplicativo mobile', r'android|\bios\b|aplicativo mobile|mobile app|ios app', 60, 120),
    ('avaliação de IA e dados', r'\bia\b|\bartificial intelligence\b|machine learning|lora|comfyui', 24, 64),
]


def build_quote(snapshot, profile, settings=None):
    if settings is None:
        pricing_path = Path(getattr(profile, 'pricing_path', '') or 'config/pricing.json')
        if not pricing_path.is_absolute():
            pricing_path = ROOT / pricing_path
        settings = json.loads(pricing_path.read_text(encoding='utf-8'))
    currency = detect_currency(snapshot, profile, settings)
    overrides = settings.get('currency_overrides', {}).get(currency, {})
    if overrides:
        settings = dict(settings)
        settings.update(overrides)
    settings['currency'] = currency
    english = str(getattr(profile, 'locale', '')).lower().startswith('en')
    for key in ('hourly_rate', 'minimum_price', 'productive_team_hours_per_day'):
        if not math.isfinite(settings[key]) or settings[key] <= 0:
            raise ValueError(f'Invalid {key}')
    price_floor = settings.get('proposal_price_floor', settings['minimum_price'])
    price_ceiling = settings.get('proposal_price_ceiling', float('inf'))
    valid_floor = isinstance(price_floor, (int, float)) and math.isfinite(price_floor) and price_floor > 0
    valid_ceiling = isinstance(price_ceiling, (int, float)) and price_ceiling > 0 and (math.isfinite(price_ceiling) or math.isinf(price_ceiling))
    if not valid_floor or not valid_ceiling or price_ceiling < price_floor:
        raise ValueError('Invalid proposal price band')
    if not 0 <= settings['fee_fraction'] < 1:
        raise ValueError('Invalid fee_fraction')
    for key in ('risk_buffer', 'qa_fraction', 'feedback_business_days'):
        if not math.isfinite(settings[key]) or settings[key] < 0:
            raise ValueError(f'Invalid {key}')
    adjustments = settings.get('adjustments', {})
    defaults = {
        'complexity_per_integration': 0.0, 'complexity_per_screen': 0.0,
        'urgent_deadline_days': 0, 'urgent_multiplier': 0.0,
        'recurring_client_multiplier': 0.0, 'good_history_multiplier': 0.0,
        'min_multiplier': 1.0, 'max_multiplier': 1.0,
    }
    adjustments = {key: adjustments.get(key, value) for key, value in defaults.items()}
    for key, value in adjustments.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'Invalid adjustments.{key}')
    if adjustments['min_multiplier'] <= 0 or adjustments['max_multiplier'] < adjustments['min_multiplier']:
        raise ValueError('Invalid adjustment multiplier bounds')
    title = ' '.join(str(snapshot.get('title', '')).split())[:200]
    description = str(snapshot.get('description', ''))
    confirmed_context = [str(item).strip() for item in snapshot.get('confirmed_context', []) if str(item).strip()]
    if confirmed_context:
        description += '\nRestrições confirmadas pelo cliente:\n' + '\n'.join(confirmed_context[-20:])
    text = normalized(title + ' ' + description)
    integration_count = snapshot.get('integrations_count')
    if not isinstance(integration_count, (int, float)):
        integration_count = len(re.findall(r'integrac|webhook|conexao com|terceiro', text))
    screen_count = snapshot.get('screens_count')
    if not isinstance(screen_count, (int, float)):
        quantities = re.findall(r'\b(\d+)\s+(?:telas|paginas|landing pages|endpoints)\b', text)
        screen_count = sum(int(value) for value in quantities)
        if screen_count == 0:
            screen_count = len(re.findall(r'\b(?:tela|pagina|dashboard|painel)\b', text))
    factor = 1.0 + float(integration_count) * adjustments['complexity_per_integration']
    factor += float(screen_count) * adjustments['complexity_per_screen']
    deadline = snapshot.get('deadline_days')
    if isinstance(deadline, (int, float)) and adjustments['urgent_deadline_days'] > 0 and deadline <= adjustments['urgent_deadline_days']:
        factor += adjustments['urgent_multiplier']
    history = snapshot.get('client_history') or {}
    if history.get('recurring'):
        factor += adjustments['recurring_client_multiplier']
    if history.get('good_history'):
        factor += adjustments['good_history_multiplier']
    factor = min(adjustments['max_multiplier'], max(adjustments['min_multiplier'], factor))
    effective_hourly_rate = settings['hourly_rate'] * factor
    tasks = [dict(deliverable=name, hours_min=low, hours_max=high)
             for name, pattern, low, high in RULES if re.search(pattern, text)]
    questions = []
    if len(description.split()) < 35 or not tasks:
        questions.append('What functionality, inputs, outputs, and acceptance criteria are included in this stage?' if english else 'Quais são as funcionalidades, entradas, saídas e critérios de aceite desta etapa?')
    if re.search(r'integrac|integration|\bapi\b|webhook|automacao|automation', text):
        questions.append('Which systems need to be integrated, and are documentation, access, and sample data available?' if english else 'Quais sistemas serão integrados e já existem documentação, acessos e exemplos de dados?')
    if re.search(r'legado|legacy|refator|refactor|migracao|migration|correc|\bbug\b', text):
        questions.append('Can we review the current code and reproduce the issue before defining the first milestone?' if english else 'Podemos avaliar o código atual e reproduzir o problema antes de fechar o valor?')
    if re.search(r'\bsaas\b|marketplace|completo|\bcomplete\b|\bia\b|\bai\b|lora|comfyui', text):
        questions.append('Which features belong in the first MVP, and which should be deferred to later milestones?' if english else 'Quais funcionalidades entram no primeiro MVP e quais ficam para etapas posteriores?')
    if re.search(r'pagamento|payment|checkout|\bpix\b|boleto|cartao|card', text):
        questions.append('Which payment gateway will be used, and which payment methods, billing rules, payouts, and reconciliation are in scope?' if english else 'Qual gateway de pagamento será usado e quais meios, regras de cobrança, repasse e conciliação entram no escopo?')
    if re.search(r'plataforma|platform', text) and not re.search(r'criterios de aceite|acceptance criteria|fluxos detalhados|detailed flows|documentacao funcional|functional documentation', text):
        questions.append('Which user roles, screens, and workflows are included in the first platform milestone?' if english else 'Quais perfis de usuário, telas e fluxos entram na primeira entrega da plataforma?')
    if re.search(r'landing pages', text):
        questions.append('How many landing pages are required, and will the client provide the layout, copy, images, domain, and form rules?' if english else 'Quantas landing pages serão entregues e o cliente fornecerá layout, textos, imagens, domínio e regras dos formulários?')
    elif re.search(r'site profissional|website|business site', text) and not re.search(r'\b\d+\s+paginas|\b\d+\s+pages|pagina unica|single page|layout aprovado|approved layout', text):
        questions.append('How many pages and sections are in scope, and who will provide the layout, copy, images, and forms?' if english else 'Quantas páginas e seções entram no site e quem fornecerá layout, textos, imagens e formulários?')
    if re.search(r'libras|lora|comfyui|android|\bios\b', text):
        questions.append('We need to confirm the required experience in this specialty before committing to the scope.' if english else 'Precisamos confirmar internamente a experiência necessária nessa especialidade.')
    if re.search(r'\bsem\b|no need|not include|already exists|already done|just fix|nao precisa|nao incluir|ja existe|ja esta pronto|apenas corrigir', text):
        questions.append('Which parts are already complete, and which deliverables should remain outside the estimate?' if english else 'Quais partes já estão prontas e quais entregas devem ficar fora do orçamento?')
    quantities = re.findall(r'\b(\d+)\s+(?:telas|pages|paginas|screens|landing pages|integracoes|integrations|endpoints|usuarios|users|produtos|products)\b', text)
    if any(int(value) > 3 for value in quantities):
        questions.append('Can we detail the screens, integrations, and volumes so each deliverable can be estimated accurately?' if english else 'Podemos detalhar as telas, integrações e volumes para estimar cada entrega?')
    blocked = snapshot.get('has_gold_badge') is True or snapshot.get('is_premium') is True
    excluded = [term for term in profile.excluded_jobs
                if re.search(r'(?<!\w)' + re.escape(normalized(term)) + r'(?!\w)', text)]
    if blocked or excluded:
        return dict(action='skip', reason='Vaga restrita ou excluída pelo perfil.', suggested_price=None,
                    estimated_days=None, questions=[], breakdown=tasks)
    if not tasks:
        low = high = None
    else:
        if english:
            labels = {
                'site institucional': 'corporate website',
                'site em CMS ou blog': 'CMS or blog setup',
                'API e persistência': 'API and data persistence',
                'interface web': 'web interface',
                'automação de fluxo': 'workflow automation',
                'autenticação e permissões': 'authentication and permissions',
                'integrações externas': 'third-party integrations',
                'pagamentos e conciliação': 'payments and reconciliation',
                'agenda e reservas': 'scheduling and bookings',
                'relatórios e painel': 'reports and admin dashboard',
                'migração e refatoração': 'migration and refactoring',
                'arquitetura multiempresa': 'multi-tenant architecture',
                'aplicativo mobile': 'mobile application',
                'avaliação de IA e dados': 'AI and data evaluation',
            }
            for task in tasks:
                task['deliverable'] = labels.get(task['deliverable'], task['deliverable'])
            tasks.append(dict(deliverable='requirements, documentation, and handoff', hours_min=6, hours_max=12))
        else:
            tasks.append(dict(deliverable='alinhamento, documentação e entrega', hours_min=6, hours_max=12))
        base_low = sum(t['hours_min'] for t in tasks)
        base_high = sum(t['hours_max'] for t in tasks)
        qa = dict(deliverable='testing and review' if english else 'testes e revisão', hours_min=math.ceil(base_low * settings['qa_fraction']),
                  hours_max=math.ceil(base_high * settings['qa_fraction']))
        tasks.append(qa)
        low = math.ceil((base_low + qa['hours_min']) * (1 + settings['risk_buffer']))
        high = math.ceil((base_high + qa['hours_max']) * (1 + settings['risk_buffer']))
    raw_prices = ([math.ceil(max(settings['minimum_price'], h * effective_hourly_rate) / 50) * 50
                   for h in (low, high)] if low is not None else None)
    prices = ([min(price_ceiling, max(price_floor, price)) for price in raw_prices]
              if raw_prices else None)
    days = ([math.ceil((math.ceil(h / settings['productive_team_hours_per_day']) +
                       settings['feedback_business_days']) * 7 / 5) for h in (low, high)]
            if low is not None else None)
    # Use the middle of the calculated range as the client-facing starting
    # point. The upper bound remains available internally for planning, but
    # presenting it for every lead makes a preliminary estimate look like a
    # deliberately high fixed bid.
    initial_price = (min(price_ceiling, max(price_floor,
                     math.ceil(((prices[0] + prices[1]) / 2) / 50) * 50))
                     if prices else None)
    initial_days = math.ceil((days[0] + days[1]) / 2) if days else None
    totals = [round(p / (1 - settings['fee_fraction']), 2) for p in prices] if prices else None
    budget = snapshot.get('budget_max')
    if isinstance(budget, (int, float)) and totals and budget < totals[0]:
        questions.append('Would the budget allow a smaller first milestone or an adjustment to the scope?' if english else 'O orçamento permite reduzir o escopo para uma primeira etapa ou ajustar o valor?')
    deadline = snapshot.get('deadline_days')
    if isinstance(deadline, (int, float)) and days and deadline < days[1]:
        questions.append('Is there flexibility in the deadline, or should we prioritize a smaller first delivery?' if english else 'Há flexibilidade no prazo ou podemos priorizar uma entrega menor?')
    advisory = commercial_advisories(snapshot, settings, high, initial_price)
    if 'off_platform_contact' in advisory['risk_ids']:
        return dict(action='skip', reason='Pedido de contato fora da plataforma exige revisão manual.',
                    suggested_price=None, estimated_days=None, questions=[], breakdown=tasks,
                    knowledge=advisory)
    action = 'question' if questions else 'proposal'
    client = ' '.join(str(snapshot.get('client', '')).split())[:80]
    team = str(getattr(profile, 'team_description', '') or '').strip()
    if english:
        team = team or 'two-developer full-stack team'
        greeting = f'Hi {client},' if client else 'Hi,'
        opening = f'{greeting} We are a {team} working together, and we are interested in the project "{title}".'
        question = (opening + ' ' + ' '.join(questions) +
                    ' Once these details are confirmed, we can agree on a fair price based on the scope and priorities. '
                    'The price is negotiable, and paid APIs, hosting, domains, and services remain in the client account.')
    else:
        if 'dois desenvolvedores full stack' not in normalized(team):
            team = 'equipe de dois desenvolvedores full stack'
        greeting = f'Olá, {client}!' if client else 'Olá!'
        opening = (f'{greeting} Somos uma {team} que trabalham juntos e temos interesse '
                   f'no projeto “{title}”.')
        question = (opening + ' ' + ' '.join(questions) +
                    ' Depois desses detalhes, o valor é a combinar conforme o escopo e as prioridades; '
                    'podemos combinar um valor justo. '
                    'Custos de APIs, hospedagem e serviços pagos ficam nas contas do cliente.')
    scope = '; '.join(t['deliverable'] for t in tasks[:5])
    if english:
        message = (opening + f' We understand the delivery includes {scope}. '
                   'We propose working in verifiable milestones covering requirements, implementation, testing, and documented handoff. '
                   'We will validate the main workflows and agree on acceptance criteria before starting. '
                   'The timeline begins after access, materials, and scope confirmation are available. '
                   'New functionality can be estimated separately. '
                   'Our proposal covers development and configuration; paid APIs, hosting, domains, and services remain in the client account.')
    else:
        message = (opening + f' Entendemos que a entrega envolve {scope}. '
                   'Propomos executar em etapas verificáveis: alinhamento dos requisitos, implementação, testes e entrega documentada. '
                   'Validamos os fluxos principais e combinamos os critérios de aceite antes de iniciar. '
                   'O prazo começa após recebermos os acessos, materiais e a confirmação do escopo. '
                   'Novas funcionalidades são orçadas separadamente. '
                   'Nossa proposta cobre desenvolvimento e configuração; APIs, hospedagem, domínio e serviços pagos ficam nas contas do cliente.')
    if action == 'proposal':
        if english:
            message += (f' As an initial proposal for this scope, we estimate {format_money(initial_price, currency)} and up to '
                        f'{initial_days} calendar days, including testing and one revision round. This is an initial estimate; '
                        'the price is negotiable based on the final details, scope, and priorities. '
                        'We can align the milestones and adjust the proposal here on Upwork.')
        else:
            message += (f' Como proposta inicial para esse escopo, indicamos {format_brl(initial_price)} e prazo de até '
                        f'{initial_days} dias corridos, incluindo testes e uma rodada de revisão. Esse é um valor de '
                        'referência. O valor pode ser combinado e o preço pode ser negociado conforme os detalhes finais, '
                        'o escopo e as prioridades '
                        'do projeto. Se fizer sentido, podemos alinhar as etapas e ajustar a proposta aqui pela plataforma.')
    fallback_scope = scope or ('service analysis, implementation, testing, and documented handoff' if english else 'análise do serviço, implementação, testes e entrega documentada')
    if prices:
        if english:
            fallback_message = (opening + f' Based on the described service, we would initially scope {fallback_scope}. '
                                f'Our preliminary estimate is {format_money(initial_price, currency)}, with up to {initial_days} calendar days. '
                                'The price is negotiable after confirming the final details, priorities, and acceptance criteria. '
                                'We can split the work into verifiable milestones with implementation, testing, and documentation. '
                                'Paid APIs, hosting, domains, and services remain in the client account.')
        else:
            fallback_message = (opening + f' Pelo serviço descrito, consideramos inicialmente {fallback_scope}. '
                                f'Com base nessas entregas, nossa estimativa preliminar é de {format_brl(initial_price)}, '
                                f'com prazo de até {initial_days} dias corridos. O preço pode ser negociado conforme os '
                                'detalhes finais, prioridades e critérios de aceite; depois de confirmar o escopo, '
                                'ajustamos a proposta. Somos flexíveis para dividir a entrega em etapas verificáveis, '
                                'com implementação, testes e documentação. APIs, hospedagem, domínio e serviços pagos '
                                'ficam nas contas do cliente.')
    else:
        if english:
            fallback_message = (opening + ' We would like to understand the functionality, inputs, outputs, and acceptance '
                                'criteria before finalizing the scope. The price is negotiable after confirmation, and we can '
                                'organize the work into verifiable milestones with implementation, testing, and documentation. '
                                'Paid APIs, hosting, domains, and services remain in the client account.')
        else:
            fallback_message = (opening + ' Queremos entender melhor as funcionalidades, entradas, saídas e critérios '
                                'de aceite antes de fechar o escopo. O preço pode ser negociado depois dessa confirmação, '
                                'e podemos organizar a entrega em etapas verificáveis com implementação, testes e '
                                'documentação. APIs, hospedagem, domínio e serviços pagos ficam nas contas do cliente.')
    return dict(action=action, subject=f'Proposal: {title}' if english else f'Proposta: {title}', message=message, question=question,
                suggested_price=initial_price if prices and action == 'proposal' else None,
                fallback_message=fallback_message, fallback_price=initial_price,
                estimated_days=initial_days if action == 'proposal' else None, fallback_days=initial_days,
                estimated_hours=high, hours_range=[low, high], price_range=prices,
                client_total_range=totals, days_range=days, questions=questions, breakdown=tasks,
                assumptions=settings, knowledge=advisory,
                estimate_type='preliminar por regras; referências de mercado são apenas sanity check',
                currency=currency,
                pricing_factors={
                    'integration_count': int(integration_count), 'screen_count': int(screen_count),
                    'multiplier': round(factor, 4), 'effective_hourly_rate': round(effective_hourly_rate, 2),
                    'client_history': history, 'confirmed_context_count': len(confirmed_context),
                })

from __future__ import annotations
import hashlib, json, math, os, re, sqlite3, threading, uuid, time, unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlsplit

def now(): return datetime.now(timezone.utc).isoformat()
def sha(value: str): return hashlib.sha256(value.encode()).hexdigest()

def normalize_client_key(value):
    """Return one stable identity for a client URL or displayed name."""
    if value is None:
        return ''
    text = ' '.join(str(value).split()).strip()
    if not text:
        return ''
    if '://' in text:
        parsed = urlsplit(text)
        if parsed.netloc:
            host = parsed.netloc.lower()
            if host.startswith('www.'):
                host = host[4:]
            path = re.sub(r'/users?/', '/user/', parsed.path.rstrip('/'), flags=re.IGNORECASE)
            return f'{host}{path}'
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower())
                   if not unicodedata.combining(c))

def stable_proposal_key(url='', external_id='', title='', client_key='', profile_id='default'):
    """Build an idempotency key that does not change when the copy is regenerated."""
    identity = normalize_client_key(url)
    if not identity:
        identity = '|'.join((normalize_client_key(external_id), normalize_client_key(title), normalize_client_key(client_key)))
    return sha('|'.join(('freelahunter-proposal-v2', identity, str(profile_id))))

def classify_price(value):
    if value is None: return 'não classificado'
    if value < 1000: return 'até R$ 999'
    if value < 2500: return 'R$ 1.000–2.499'
    if value < 5000: return 'R$ 2.500–4.999'
    return 'R$ 5.000 ou mais'

def classify_project_type(value):
    text = str(value or '').lower()
    for label, terms in (
        ('landing page', ('landing page',)),
        ('automação', ('automação', 'automacao', 'n8n', 'bot')),
        ('API/backend', ('api', 'backend', 'back-end', 'fastapi')),
        ('frontend/site', ('frontend', 'front-end', 'react', 'next.js', 'site', 'website')),
        ('mobile', ('android', 'ios', 'aplicativo mobile')),
        ('SaaS/marketplace', ('saas', 'marketplace')),
        ('integração', ('integração', 'integracao', 'webhook')),
    ):
        if any(term in text for term in terms): return label
    return 'outro'

class JobStatus(str, Enum):
    NEW='NEW'; FILTERED='FILTERED'; ANALYZING='ANALYZING'; ANALYZED='ANALYZED'; REJECTED='REJECTED'; PROPOSAL_GENERATING='PROPOSAL_GENERATING'; PROPOSAL_GENERATED='PROPOSAL_GENERATED'; REVIEW_REQUIRED='REVIEW_REQUIRED'; APPROVED='APPROVED'; READY_TO_SEND='READY_TO_SEND'; SENDING='SENDING'; SENT='SENT'; SEND_FAILED='SEND_FAILED'; CLIENT_REPLIED='CLIENT_REPLIED'; ARCHIVED='ARCHIVED'; ERROR='ERROR'

@dataclass
class Job:
    title: str; description: str; platform: str='mock'; external_id: str=''; url: str=''; budget_min: float|None=None; budget_max: float|None=None; currency: str='BRL'; skills: list[str]=field(default_factory=list); category: str=''; client: str=''; published_at: str|None=None; id: int|None=None; status: JobStatus=JobStatus.NEW; content_hash: str=''; first_seen_at: str|None=None; last_seen_at: str|None=None; deadline_days: float|None=None; client_key: str=''; client_history: dict=field(default_factory=dict)
    def canonical(self): return self.url.lower().split('#')[0].rstrip('/') if self.url else f'{self.platform}:{self.external_id}'
    def compute_hash(self):
        title=' '.join(self.title.lower().split()); desc=' '.join(self.description.lower().split()); budget=f'{self.budget_min}:{self.budget_max}'
        self.content_hash=sha(title+desc+budget); return self.content_hash

@dataclass
class Profile:
    name: str=''; headline: str=''; bio: str=''; skills: list[str]=field(default_factory=list); experience: list[str]=field(default_factory=list); education: str=''; portfolio: list[str]=field(default_factory=list); preferred_jobs: list[str]=field(default_factory=list); excluded_jobs: list[str]=field(default_factory=list); minimum_budget: float|None=None; languages: list[str]=field(default_factory=list); team_description: str='equipe de desenvolvedores full stack'; client_pays_paid_services: bool=True; id: str='default'
    locale: str='pt-BR'; currency: str='BRL'; pricing_path: str='config/pricing.json'; knowledge_path: str='knowledge/team-devs-senior-full-stack.md'

@dataclass
class PortfolioProject:
    name: str; description: str; technologies: list[str]=field(default_factory=list); category: str=''; keywords: list[str]=field(default_factory=list); repository_url: str=''; demo_url: str=''

@dataclass
class Analysis:
    score: int; recommended: bool; difficulty: str; matched_skills: list[str]; missing_skills: list[str]; requirements: list[str]; risks: list[str]; relevant_projects: list[str]; estimated_hours_min: int; estimated_hours_max: int; confidence: float; reason: str

@dataclass
class ProposalDraft:
    job_id: int; subject: str; message: str; estimated_hours: int; suggested_price: float|None; questions: list[str]; generated_at: str=field(default_factory=now); generation_model: str='mock'; validation_status: str='PENDING'; id: int|None=None

class ProviderCapabilities:
    def __init__(self, search=True, details=True, authorized_send=False, status_tracking=False): self.search=search; self.details=details; self.authorized_send=authorized_send; self.status_tracking=status_tracking

class JobProvider(Protocol):
    name: str; capabilities: ProviderCapabilities
    def search(self) -> list[Job]: ...

class MockProvider:
    name='mock'; capabilities=ProviderCapabilities()
    def __init__(self, jobs=None): self.jobs=jobs or [Job(title='API REST em FastAPI', description='Criar API REST com Python FastAPI e SQL', external_id='mock-1', url='https://mock/jobs/1', budget_min=800, budget_max=1500, skills=['Python','FastAPI','SQL'])]
    def search(self): return self.jobs

class AuthorizedMockProvider(MockProvider):
    name='authorized_mock'; capabilities=ProviderCapabilities(authorized_send=True, status_tracking=True)

class ProviderRegistry:
    def __init__(self): self._providers={}
    def register(self, name, provider): self._providers[name]=provider
    def get(self, name): return self._providers[name]
    def enabled(self): return list(self._providers.values())

class ProfileService:
    def __init__(self, path='config/profile.yaml'): self.path=path
    def load(self):
        p=Profile(name='FreelaHunter User', headline='Desenvolvedor Full Stack | Python, APIs e Automação', skills=['Python','Java','JavaScript','TypeScript','HTML','CSS','React','Next.js','FastAPI','APIs REST','SQL','Linux','Git','GitHub','Automação','Integrações','IA'])
        if os.path.exists(self.path):
            import yaml
            with open(self.path, encoding='utf-8') as handle: data=yaml.safe_load(handle) or {}
            for k,v in data.items():
                if hasattr(p,k): setattr(p,k,v)
        return p

class PortfolioService:
    def __init__(self, projects=None): self.projects=projects or [PortfolioProject('API FastAPI', 'API REST para automação e integrações', ['Python','FastAPI','SQL'], 'backend', ['api','rest','automação'])]
    def relevant(self, job, profile):
        terms={x.lower() for x in job.skills + re.findall(r'[A-Za-z]+',job.title+' '+job.description)}
        return [p for p in self.projects if terms & {x.lower() for x in p.technologies+p.keywords}]

class FilterService:
    @staticmethod
    def _normalize(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value.lower()) if not unicodedata.combining(c))

    @classmethod
    def _matches(cls, text, term):
        normalized_text = cls._normalize(text)
        normalized_term = cls._normalize(term).strip()
        if not normalized_term:
            return False
        return re.search(r'(?<!\w)' + re.escape(normalized_term) + r'(?!\w)', normalized_text) is not None

    def __init__(self, included_keywords=None, excluded_keywords=None, minimum_budget=None, maximum_budget=None): self.included=set(x.lower() for x in (included_keywords or [])); self.excluded=set(x.lower() for x in (excluded_keywords or [])); self.minimum_budget=minimum_budget; self.maximum_budget=maximum_budget
    def accepts(self, job):
        text=job.title+' '+job.description+' '+' '.join(job.skills)
        if self.excluded and any(self._matches(text, x) for x in self.excluded): return False
        if self.included and not any(self._matches(text, x) for x in self.included): return False
        if self.minimum_budget is not None and job.budget_max is not None and job.budget_max<self.minimum_budget: return False
        if self.maximum_budget is not None and (job.budget_min or 0)>self.maximum_budget: return False
        return True

class AIService:
    def analyze(self, job, profile, projects):
        from .quoting import build_quote
        quote = build_quote({'title': job.title, 'description': job.description,
                             'deadline_days': job.deadline_days,
                             'client_history': job.client_history}, profile)
        text = job.title + ' ' + job.description
        ps={x.lower() for x in profile.skills}; js={x.lower() for x in job.skills}
        matched=sorted({skill for skill in ps if FilterService._matches(text, skill)} | (js & ps))
        missing=sorted(js-ps); score=max(0,min(100, 50+len(matched)*15-len(missing)*8)); hours=max(4, len(job.skills)*3)
        if quote.get('hours_range') and quote['hours_range'][0] is not None:
            low, high = quote['hours_range']
            return Analysis(score, score>=60, 'hard' if high > 100 else 'medium', matched, missing,
                            [t['deliverable'] for t in quote['breakdown']], quote['questions'],
                            [], low, high, .85, 'Estimativa preliminar por entregas; confirmar escopo.')
        return Analysis(score, score>=60, 'hard' if len(job.skills)>5 else 'medium', matched, missing, job.skills, [], [p.name for p in projects], hours, hours+7, .85, f'{len(matched)} competências compatíveis; escopo requer validação.')

class ProposalGenerator:
    def generate(self, job, analysis, profile, projects, price=None):
        from .quoting import build_quote
        quote = build_quote({'title': job.title, 'description': job.description,
                             'budget_max': job.budget_max, 'deadline_days': job.deadline_days,
                             'client_history': job.client_history}, profile)
        if quote['action'] == 'skip':
            raise ValueError(quote['reason'])
        message = quote['question'] if quote['action'] == 'question' else quote['message']
        return ProposalDraft(job.id or 0, quote['subject'], message,
                             quote['estimated_hours'] or 0, quote['suggested_price'],
                             quote['questions'])

class ProposalValidator:
    BAD=['[CLIENT]','[PROJECT]','TODO','INSERT HERE','{{name}}']
    NEGOTIATION_TERMS=('podemos combinar o preço', 'podemos combinar um valor',
                       'podemos negociar o valor', 'valor pode ser negociado',
                       'valor pode ser combinado', 'preço pode ser negociado',
                       'preço inicial')

    @staticmethod
    def _price_variants(value, currency='BRL'):
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return ()
        if not math.isfinite(amount) or amount < 0:
            return ()
        if str(currency).upper() == 'USD':
            return f'$ {amount:,.2f}'.lower(), f'${amount:,.2f}'.lower(), f'usd {amount:,.2f}'.lower()
        english = f'R$ {amount:,.2f}'
        brazilian = english.replace(',', '_').replace('.', ',').replace('_', '.')
        return english.lower(), brazilian.lower()

    def validate(self, proposal, profile):
        m=proposal.message.strip(); low=m.lower(); reasons=[]
        if not 100 <= len(m.split()) <= 220: reasons.append('tamanho fora de 100-220 palavras')
        if not m or not proposal.subject: reasons.append('campo vazio')
        if any(x.lower() in low for x in self.BAD): reasons.append('placeholder')
        english = str(getattr(profile, 'locale', '')).lower().startswith('en')
        team = str(getattr(profile, 'team_description', '') or '').strip().lower()
        team_terms = [team] if team else []
        team_terms += ['two-developer full-stack team', 'two full-stack developers'] if english else ['dois desenvolvedores full stack', 'equipe de dois desenvolvedores full stack']
        if not any(term in low for term in team_terms):
            reasons.append('proposal does not present the two-developer full-stack team' if english else 'proposta não apresenta a equipe de dois desenvolvedores full stack')
        negotiation_terms = ('we can negotiate the price', 'the price is negotiable', 'price is negotiable', 'initial estimate') if english else self.NEGOTIATION_TERMS
        if not any(term in low for term in negotiation_terms):
            reasons.append('proposal does not say that the price is negotiable' if english else 'proposta não informa que o valor pode ser negociado')
        if proposal.suggested_price is not None:
            variants = self._price_variants(proposal.suggested_price, getattr(profile, 'currency', 'BRL'))
            if not variants or not any(variant in low for variant in variants):
                reasons.append('proposal does not present the suggested price' if english else 'proposta não apresenta o preço sugerido')
        known={x.lower() for x in profile.skills}
        # only flag obvious unsupported technology claims
        if any(t in low for t in ['django','kubernetes']) and not any(t in known for t in ['django','kubernetes']): reasons.append('tecnologia não presente no perfil')
        proposal.validation_status='PASSED' if not reasons else 'FAILED'; return proposal.validation_status, reasons

class PriceEstimator:
    def __init__(self, hourly_rate=None, minimum_project_price=None, preferred_project_price=None, currency='BRL'): self.hourly_rate=hourly_rate; self.minimum=minimum_project_price; self.preferred=preferred_project_price; self.currency=currency
    def estimate(self, hours, complexity='medium', budget_max=None, adjustment_factor=1.0):
        if self.hourly_rate is None and self.minimum is None and budget_max is None: return None, 'informações insuficientes'
        if not isinstance(adjustment_factor, (int, float)) or adjustment_factor <= 0:
            raise ValueError('fator de ajuste inválido')
        value=(hours*self.hourly_rate*adjustment_factor if self.hourly_rate else self.preferred or self.minimum or budget_max)
        if self.minimum: value=max(value,self.minimum)
        if budget_max is not None and value > budget_max:
            return round(value,2), 'orçamento insuficiente; negociar escopo sem reduzir o preço calculado'
        return round(value,2), 'estimativa baseada em horas, taxa e orçamento anunciado'

class MockSender:
    def __init__(self): self.sent=[]
    def validate_credentials(self): return True
    def can_send(self): return True
    def send(self, proposal): self.sent.append(proposal); return {'external_id':str(uuid.uuid4()),'status':'SENT'}
    def get_status(self, external_id): return 'SENT'

class PipelineError(Exception):
    """Raised when a job cannot complete a safe pipeline stage."""

class NotificationService:
    def notify(self, event, payload):
        return {'event': event, 'payload': payload, 'delivered': False}

class CircuitBreaker:
    def __init__(self, failures=3, cooldown_seconds=1800): self.failures=failures; self.cooldown=cooldown_seconds; self._state={}
    def allow(self, provider):
        row=self._state.get(provider); return not row or row[1] <= time.time()
    def record_success(self, provider): self._state.pop(provider,None)
    def record_failure(self, provider, status=None):
        count, until=self._state.get(provider,(0,0)); count += 1
        self._state[provider]=(count, time.time()+self.cooldown if count>=self.failures else 0)

class Config:
    def __init__(self, environ=None):
        e=environ or os.environ
        def b(k,d): return e.get(k,d).lower() in ('1','true','yes','on')
        self.dry_run=b('DRY_RUN','true'); self.auto_send=b('AUTO_SEND','false'); self.kill_switch=b('AUTO_SEND_KILL_SWITCH','true'); self.automation_mode=e.get('AUTOMATION_MODE','SEMI_AUTO'); self.interval=int(e.get('HUNT_INTERVAL_MINUTES','15')); self.min_score=int(e.get('AUTO_SEND_MIN_SCORE','85')); self.min_confidence=float(e.get('MIN_CONFIDENCE','0.75')); self.max_hour=int(e.get('MAX_PROPOSALS_PER_HOUR','3')); self.max_day=int(e.get('MAX_PROPOSALS_PER_DAY','10')); self.pause_no_response_days=int(e.get('PAUSE_NO_RESPONSE_DAYS','7')); self.pause_consecutive_no_response=int(e.get('PAUSE_CONSECUTIVE_NO_RESPONSE','5')); self.pause_rejection_threshold=float(e.get('PAUSE_REJECTION_THRESHOLD','0.6'))
    def warnings(self):
        out=[]
        if self.automation_mode=='AUTO' and self.dry_run: out.append('AUTO MODE configured but DRY_RUN prevents external sending.')
        if self.auto_send and self.kill_switch: out.append('AUTO SEND disabled by kill switch.')
        return out

class SendPolicyEngine:
    def __init__(self, auto_send=False, dry_run=True, kill_switch=True, min_score=85, min_confidence=.75): self.auto_send=auto_send; self.dry_run=dry_run; self.kill_switch=kill_switch; self.min_score=min_score; self.min_confidence=min_confidence
    def decide(self, provider, analysis, proposal, sender, already_sent=False, within_limits=True):
        if already_sent: return 'BLOCKED'
        if proposal.questions or proposal.suggested_price is None: return 'REVIEW_REQUIRED'
        if not provider.capabilities.authorized_send or not self.auto_send or self.dry_run or self.kill_switch: return 'REVIEW_REQUIRED'
        if analysis.score<self.min_score or analysis.confidence<self.min_confidence or proposal.validation_status!='PASSED' or not within_limits or not sender.validate_credentials() or not sender.can_send(): return 'REVIEW_REQUIRED'
        return 'AUTO_SEND'


class RuntimeControl:
    """Mutable process controls shared by API and scheduler."""

    def __init__(self, kill_switch: bool = True):
        self.kill_switch = kill_switch

    def set_kill_switch(self, enabled: bool) -> bool:
        self.kill_switch = enabled
        return self.kill_switch

class Database:
    OUTCOME_STATUSES = {'draft', 'awaiting_response', 'responded', 'accepted', 'rejected', 'no_response', 'cancelled'}

    def __init__(self, path=None):
        path = path or os.getenv('DATABASE_PATH') or 'freelahunter.db'
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._create_base_schema()
        self._migrate_schema()

    def _create_base_schema(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, provider TEXT, external_id TEXT, canonical_url TEXT,
            title TEXT, description TEXT, budget_min REAL, budget_max REAL,
            currency TEXT, skills TEXT, status TEXT, content_hash TEXT,
            first_seen_at TEXT, last_seen_at TEXT,
            UNIQUE(provider,external_id)
        )''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY, job_id INTEGER, idempotency_key TEXT UNIQUE,
            subject TEXT, message TEXT, validation_status TEXT, status TEXT,
            suggested_price REAL, sent_external_id TEXT
        )''')
        self.conn.execute('CREATE TABLE IF NOT EXISTS limits (bucket TEXT PRIMARY KEY, count INTEGER, reset_at TEXT)')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY, event TEXT, run_id TEXT, job_id INTEGER,
            created_at TEXT, details TEXT
        )''')
        self.conn.execute('CREATE TABLE IF NOT EXISTS limit_events (id INTEGER PRIMARY KEY, bucket TEXT, created_at REAL)')
        self.conn.commit()

    def _migrate_schema(self):
        self.conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        columns = {row[1] for row in self.conn.execute('PRAGMA table_info(proposals)')}
        additions = {
            'sent_at': 'TEXT', 'response_at': 'TEXT',
            'outcome_status': "TEXT NOT NULL DEFAULT 'draft'",
            'outcome_updated_at': 'TEXT', 'project_type': 'TEXT',
            'conversation_id': 'TEXT', 'client_key': 'TEXT', 'price_band': 'TEXT',
            'send_kind': "TEXT NOT NULL DEFAULT 'proposal'",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.conn.execute(f'ALTER TABLE proposals ADD COLUMN {name} {definition}')
        job_columns = {row[1] for row in self.conn.execute('PRAGMA table_info(jobs)')}
        for name, definition in {'deadline_days': 'REAL', 'client_key': 'TEXT', 'client_history': "TEXT NOT NULL DEFAULT '{}'"}.items():
            if name not in job_columns:
                self.conn.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS proposal_events (
            id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL, event TEXT NOT NULL,
            event_at TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(proposal_id) REFERENCES proposals(id)
        )''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, proposal_id INTEGER,
            job_id INTEGER, direction TEXT NOT NULL, message TEXT NOT NULL,
            message_at TEXT NOT NULL, is_scope_context INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(proposal_id) REFERENCES proposals(id),
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS automation_state (
            id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
            reason TEXT, paused_at TEXT, updated_at TEXT NOT NULL
        )''')
        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_proposals_outcome ON proposals(outcome_status)''')
        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_proposals_sent_at ON proposals(sent_at)''')
        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_messages_conversation ON conversation_messages(conversation_id)''')
        self.conn.execute('''INSERT OR IGNORE INTO automation_state(id, paused, updated_at) VALUES (1, 0, ?)''', (now(),))
        self.conn.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)', (now(),))
        for row_id, client_key in self.conn.execute('SELECT id,client_key FROM proposals WHERE client_key IS NOT NULL').fetchall():
            normalized = normalize_client_key(client_key)
            if normalized != client_key:
                self.conn.execute('UPDATE proposals SET client_key=? WHERE id=?', (normalized, row_id))
        self.conn.commit()
    def upsert_job(self,j):
        j.compute_hash(); t=now()
        with self.lock:
            row=self.conn.execute('SELECT id,content_hash FROM jobs WHERE provider=? AND external_id=?',(j.platform,j.external_id)).fetchone()
            if row:
                j.id=row[0]
                self.conn.execute('''UPDATE jobs SET last_seen_at=?,content_hash=?,description=?,title=?,
                    budget_min=?,budget_max=?,skills=?,deadline_days=?,client_key=?,client_history=? WHERE id=?''',
                    (t,j.content_hash,j.description,j.title,j.budget_min,j.budget_max,json.dumps(j.skills),
                     j.deadline_days,j.client_key,json.dumps(j.client_history or {},ensure_ascii=False),j.id))
                self.conn.commit(); return j, row[1]==j.content_hash
            cur=self.conn.execute('''INSERT INTO jobs(provider,external_id,canonical_url,title,description,budget_min,budget_max,
                currency,skills,status,content_hash,first_seen_at,last_seen_at,deadline_days,client_key,client_history)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (j.platform,j.external_id,j.canonical(),j.title,j.description,j.budget_min,j.budget_max,j.currency,
                 json.dumps(j.skills),j.status.value,j.content_hash,t,t,j.deadline_days,j.client_key,
                 json.dumps(j.client_history or {},ensure_ascii=False)))
            j.id=cur.lastrowid; self.conn.commit(); return j,False
    def already_sent(self,key):
        """Return true when this proposal was sent or reserved by another worker."""
        return self.conn.execute(
            "SELECT 1 FROM proposals WHERE idempotency_key=? AND status IN ('SENT', 'SENDING')",
            (key,),
        ).fetchone() is not None

    def claim_send(self, key, maximum_per_client=1, window_seconds=86400):
        """Atomically reserve a proposal before an external send/click.

        Keeping an uncertain attempt in SENDING is intentional: retrying after a
        timeout could create a duplicate message on the platform.
        """
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                row = self.conn.execute(
                    'SELECT status,client_key FROM proposals WHERE idempotency_key=?', (key,)
                ).fetchone()
                if not row or row[0] in {'SENT', 'SENDING'}:
                    self.conn.rollback()
                    return False
                client_key = normalize_client_key(row[1])
                if client_key and maximum_per_client > 0:
                    cutoff = str(int(time.time() - window_seconds))
                    count = self.conn.execute('''SELECT COUNT(*) FROM proposals
                        WHERE status IN ('SENT','SENDING') AND client_key=?
                        AND (status='SENDING' OR strftime('%s', sent_at) >= ?)''',
                        (client_key, cutoff)).fetchone()[0]
                    if count >= maximum_per_client:
                        self.conn.rollback()
                        return False
                cursor = self.conn.execute(
                    '''UPDATE proposals SET status='SENDING'
                       WHERE idempotency_key=? AND status NOT IN ('SENT', 'SENDING')''',
                    (key,),
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception:
                self.conn.rollback()
                raise
    def save_proposal(self,p,key,status='GENERATED', project_type=None, client_key=None, conversation_id=None, price_band=None, send_kind='proposal'):
        project_type = classify_project_type(project_type or p.subject)
        client_key = normalize_client_key(client_key) or None
        if send_kind not in {'proposal', 'message'}:
            raise ValueError('send_kind inválido')
        self.conn.execute('''INSERT INTO proposals(
            job_id,idempotency_key,subject,message,validation_status,status,suggested_price,
            project_type,client_key,conversation_id,price_band,send_kind
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO UPDATE SET
            subject=excluded.subject, message=excluded.message,
            validation_status=excluded.validation_status,
            status=CASE WHEN proposals.status IN ('SENT', 'SENDING') THEN proposals.status ELSE excluded.status END,
            suggested_price=excluded.suggested_price,
            project_type=COALESCE(excluded.project_type, proposals.project_type),
            client_key=COALESCE(excluded.client_key, proposals.client_key),
            conversation_id=COALESCE(excluded.conversation_id, proposals.conversation_id),
            price_band=COALESCE(excluded.price_band, proposals.price_band),
            send_kind=CASE WHEN proposals.status IN ('SENT', 'SENDING') THEN proposals.send_kind ELSE excluded.send_kind END
        ''', (p.job_id or None,key,p.subject,p.message,p.validation_status,status,p.suggested_price,project_type,client_key,conversation_id,price_band or classify_price(p.suggested_price),send_kind))
        self.conn.commit()
        return self.conn.execute('SELECT id FROM proposals WHERE idempotency_key=?', (key,)).fetchone()[0]

    def register_proposal(self, job_id, key, subject, message, price=None, project_type=None,
                          client_key=None, conversation_id=None, validation_status='PENDING', send_kind='proposal'):
        proposal = ProposalDraft(job_id or 0, subject, message, 0, price, [], validation_status=validation_status)
        return self.save_proposal(proposal, key, 'GENERATED', project_type, client_key, conversation_id,
                                  classify_price(price), send_kind)

    def mark_sent(self,key,external_id,conversation_id=None):
        sent_at = now()
        with self.lock:
            current = self.conn.execute(
                'SELECT id,status FROM proposals WHERE idempotency_key=?', (key,)
            ).fetchone()
            if not current or current[1] == 'SENT':
                return False
            cursor = self.conn.execute('''UPDATE proposals SET status='SENT', sent_external_id=?, sent_at=?, conversation_id=COALESCE(?, conversation_id),
                outcome_status=CASE WHEN outcome_status='draft' THEN 'awaiting_response' ELSE outcome_status END,
                outcome_updated_at=? WHERE idempotency_key=? AND status <> 'SENT' ''',
                (external_id,sent_at,conversation_id,sent_at,key))
            if cursor.rowcount != 1:
                self.conn.commit()
                return False
        row = self.conn.execute('SELECT id FROM proposals WHERE idempotency_key=?', (key,)).fetchone()
        if row:
            self.record_proposal_event(row[0], 'proposal_sent', {'external_id': external_id})
        self.conn.commit()
        return True

    def record_proposal_event(self, proposal_id, event, details=None, event_at=None):
        self.conn.execute('INSERT INTO proposal_events(proposal_id,event,event_at,details) VALUES(?,?,?,?)',
                          (proposal_id,event,event_at or now(),json.dumps(details or {}, ensure_ascii=False)))

    def set_proposal_outcome(self, proposal_id, outcome, reason=None):
        if outcome not in self.OUTCOME_STATUSES:
            raise ValueError(f'outcome inválido: {outcome}')
        changed = now()
        response_at = changed if outcome in {'responded', 'accepted', 'rejected'} else None
        self.conn.execute('''UPDATE proposals SET outcome_status=?, outcome_updated_at=?,
            response_at=COALESCE(?, response_at) WHERE id=?''', (outcome,changed,response_at,proposal_id))
        self.record_proposal_event(proposal_id, f'outcome_{outcome}', {'reason': reason} if reason else {})
        self.conn.commit()
        self.evaluate_pause()

    def record_message(self, conversation_id, message, direction='inbound', proposal_id=None,
                       job_id=None, message_at=None, is_scope_context=False):
        if direction not in {'inbound', 'outbound'}:
            raise ValueError('direction deve ser inbound ou outbound')
        if proposal_id is None:
            linked = self.conn.execute('''SELECT id,job_id FROM proposals
                WHERE conversation_id=? ORDER BY id DESC LIMIT 1''', (conversation_id,)).fetchone()
            if linked:
                proposal_id, job_id = linked
        duplicate = self.conn.execute('''SELECT id FROM conversation_messages
            WHERE conversation_id=? AND direction=? AND message=? ORDER BY id DESC LIMIT 1''',
            (conversation_id, direction, message)).fetchone()
        if duplicate:
            return duplicate[0]
        self.conn.execute('''INSERT INTO conversation_messages(
            conversation_id,proposal_id,job_id,direction,message,message_at,is_scope_context
        ) VALUES(?,?,?,?,?,?,?)''', (conversation_id,proposal_id,job_id,direction,message,
                                      message_at or now(),int(is_scope_context)))
        if proposal_id and direction == 'inbound':
            received_at = message_at or now()
            self.conn.execute("UPDATE proposals SET outcome_status='responded', response_at=COALESCE(response_at, ?), outcome_updated_at=? WHERE id=?",
                              (received_at,now(),proposal_id))
            self.record_proposal_event(proposal_id, 'client_response', {'conversation_id': conversation_id})
        self.conn.commit()
        if proposal_id and direction == 'inbound': self.evaluate_pause()
        return self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]

    def get_automation_state(self):
        return self.conn.execute('SELECT paused,reason,paused_at,updated_at FROM automation_state WHERE id=1').fetchone()

    def set_automation_pause(self, paused, reason=None):
        self.conn.execute('''UPDATE automation_state SET paused=?, reason=?, paused_at=?, updated_at=? WHERE id=1''',
                          (int(paused), reason if paused else None, now() if paused else None, now()))
        self.conn.commit()

    def evaluate_pause(self, no_response_days=7, consecutive_limit=5, rejection_threshold=.6):
        if self.get_automation_state()[0]:
            return True
        cutoff = datetime.now(timezone.utc).timestamp() - no_response_days * 86400
        old_pending = self.conn.execute('''SELECT outcome_status FROM proposals
            WHERE sent_at IS NOT NULL AND outcome_status='awaiting_response'
            AND strftime('%s', sent_at) < ? ORDER BY sent_at DESC''', (cutoff,)).fetchall()
        consecutive = 0
        for row in old_pending:
            consecutive += 1
            if consecutive >= consecutive_limit:
                self.set_automation_pause(True, f'{consecutive} propostas sem resposta há {no_response_days} dias')
                return True
        total = self.conn.execute("SELECT COUNT(*) FROM proposals WHERE outcome_status IN ('accepted','rejected')").fetchone()[0]
        rejected = self.conn.execute("SELECT COUNT(*) FROM proposals WHERE outcome_status='rejected'").fetchone()[0]
        if total and rejected / total >= rejection_threshold:
            self.set_automation_pause(True, f'taxa de rejeição {rejected / total:.1%} acima do limite')
            return True
        return False

    def send_gate(self, max_per_hour=3, max_per_day=10):
        state = self.get_automation_state()
        if state and state[0]:
            return False, state[1] or 'automação pausada'
        if not self.within_limit('send-hour', max_per_hour, 3600): return False, 'limite horário atingido'
        if not self.within_limit('send-day', max_per_day, 86400): return False, 'limite diário atingido'
        return True, 'ok'

    def client_send_gate(self, client_key, maximum=1, window_seconds=86400):
        client_key = normalize_client_key(client_key)
        if not client_key:
            return True, 'cliente sem identificador'
        cutoff = str(int(time.time() - window_seconds))
        count = self.conn.execute('''SELECT COUNT(*) FROM proposals
            WHERE status IN ('SENT', 'SENDING') AND client_key IS NOT NULL AND client_key=?
            AND (status='SENDING' OR strftime('%s', sent_at) >= ?)''', (client_key, cutoff)).fetchone()[0]
        if count >= maximum:
            return False, f'limite por cliente atingido ({count}/{maximum} em 24h)'
        return True, 'ok'

    def client_message_gate(self, client_key, maximum=1, window_seconds=86400):
        """Keep automatic fallback messages to one per client per window."""
        client_key = normalize_client_key(client_key)
        if not client_key:
            return True, 'cliente sem identificador'
        cutoff = str(int(time.time() - window_seconds))
        count = self.conn.execute('''SELECT COUNT(*) FROM proposals
            WHERE status IN ('SENT', 'SENDING') AND
            (send_kind='message' OR suggested_price IS NULL)
            AND client_key IS NOT NULL AND client_key=?
            AND (status='SENDING' OR strftime('%s', sent_at) >= ?)''', (client_key, cutoff)).fetchone()[0]
        if count >= maximum:
            return False, f'limite de mensagens por cliente atingido ({count}/{maximum} em 24h)'
        return True, 'ok'

    def conversion_report(self):
        summary = self.conn.execute('''SELECT COUNT(*),
            SUM(outcome_status IN ('responded','accepted','rejected')), SUM(outcome_status='accepted'),
            SUM(outcome_status='rejected'), AVG(CASE WHEN response_at IS NOT NULL AND sent_at IS NOT NULL
              THEN (strftime('%s', response_at)-strftime('%s', sent_at))/86400.0 END)
            FROM proposals WHERE sent_at IS NOT NULL''').fetchone()
        groups = self.conn.execute('''SELECT COALESCE(project_type,'não classificado'),
            COALESCE(price_band,'não classificado'), COUNT(*),
            SUM(outcome_status='responded'), SUM(outcome_status='accepted'), SUM(outcome_status='rejected')
            FROM proposals WHERE sent_at IS NOT NULL GROUP BY project_type, price_band ORDER BY 1,2''').fetchall()
        return {'summary': summary, 'groups': groups}

    def client_history(self, client_key):
        if not client_key:
            return {'recurring': False, 'good_history': False, 'known': False}
        rows = self.conn.execute('''SELECT outcome_status FROM proposals WHERE client_key=?''', (client_key,)).fetchall()
        statuses = [row[0] for row in rows]
        completed = statuses.count('accepted')
        rejected = statuses.count('rejected')
        return {
            'recurring': bool(statuses),
            'good_history': completed > 0 or (len(statuses) >= 2 and rejected == 0),
            'known': bool(statuses), 'proposals': len(statuses),
        }

    def scope_context(self, client_key=None, conversation_id=None, limit=20):
        if not client_key and not conversation_id:
            return []
        if conversation_id:
            rows = self.conn.execute('''SELECT message FROM conversation_messages
                WHERE conversation_id=? AND is_scope_context=1 ORDER BY message_at DESC LIMIT ?''', (conversation_id, limit)).fetchall()
        else:
            rows = self.conn.execute('''SELECT m.message FROM conversation_messages m
                JOIN proposals p ON p.id=m.proposal_id WHERE p.client_key=? AND m.is_scope_context=1
                ORDER BY m.message_at DESC LIMIT ?''', (client_key, limit)).fetchall()
        return [row[0] for row in reversed(rows)]
    def within_limit(self, bucket, maximum, window_seconds=None):
        if maximum < 1: return False
        if window_seconds is None:
            row=self.conn.execute('SELECT count FROM limits WHERE bucket=?',(bucket,)).fetchone()
            return not row or row[0] < maximum
        cutoff=time.time()-window_seconds
        with self.lock:
            self.conn.execute('DELETE FROM limit_events WHERE bucket=? AND created_at<?',(bucket,cutoff))
            row=self.conn.execute('SELECT COUNT(*) FROM limit_events WHERE bucket=?',(bucket,)).fetchone()
            self.conn.commit()
        return row[0] < maximum
    def consume_limit(self, bucket):
        with self.lock:
            self.conn.execute('INSERT INTO limits(bucket,count,reset_at) VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,1,now()))
            self.conn.execute('INSERT INTO limit_events(bucket,created_at) VALUES(?,?)',(bucket,time.time()))
            self.conn.commit()
    def audit(self,event,run_id=None,job_id=None,details=None):
        created_at = now()
        payload = details or {}
        self.conn.execute('INSERT INTO activity_logs(event,run_id,job_id,created_at,details) VALUES(?,?,?,?,?)',
                          (event,run_id,job_id,created_at,json.dumps(payload, ensure_ascii=False)))
        self.conn.commit()
        if os.getenv('STRUCTURED_LOGS', 'true').lower() in ('1', 'true', 'yes', 'on'):
            print(json.dumps({'event': event, 'timestamp': created_at, 'run_id': run_id,
                              'job_id': job_id, 'details': payload}, ensure_ascii=False), flush=True)

def run_pipeline(provider, db, profile=None, auto_send=False, dry_run=True, kill_switch=True,
                 sender=None, min_score=85, min_confidence=.75, max_per_hour=3,
                 max_per_day=10, runtime_control=None, pause_no_response_days=7,
                 pause_consecutive_no_response=5, pause_rejection_threshold=.6):
    """Discover jobs and prepare proposals. Sending needs every policy gate to pass."""
    profile=profile or ProfileService().load(); portfolio=PortfolioService(); ai=AIService(); gen=ProposalGenerator(); val=ProposalValidator(); sender=sender or MockSender(); effective_kill_switch=kill_switch or bool(runtime_control and runtime_control.kill_switch); policy=SendPolicyEngine(auto_send,dry_run,effective_kill_switch,min_score,min_confidence); job_filter=FilterService(included_keywords=profile.preferred_jobs, excluded_keywords=profile.excluded_jobs, minimum_budget=profile.minimum_budget); stats={'found':0,'new':0,'duplicates':0,'filtered':0,'analyzed':0,'proposals':0,'review':0,'sent':0,'errors':0,'paused':0}
    if auto_send and db.evaluate_pause(pause_no_response_days, pause_consecutive_no_response, pause_rejection_threshold):
        stats['paused'] = 1
        state = db.get_automation_state()
        stats['pause_reason'] = state[1] if state else 'circuito de pausa ativo'
        return stats
    for job in provider.search():
        stats['found']+=1
        try:
            job.platform=provider.name; job,dup=db.upsert_job(job)
            db.audit('job_found', job_id=job.id, details={'provider': provider.name, 'title': job.title})
            if dup: stats['duplicates']+=1; db.audit('job_duplicate', job_id=job.id); continue
            stats['new']+=1
            if not job_filter.accepts(job): job.status=JobStatus.REJECTED; db.audit('job_filtered', job_id=job.id, details={'reason': 'profile_rules'}); continue
            stats['filtered']+=1; projects=portfolio.relevant(job,profile); analysis=ai.analyze(job,profile,projects); stats['analyzed']+=1; price,_=PriceEstimator(hourly_rate=100,minimum_project_price=200).estimate((analysis.estimated_hours_min+analysis.estimated_hours_max)//2, budget_max=job.budget_max); p=gen.generate(job,analysis,profile,projects,price); val.validate(p,profile); stats['proposals']+=1; key=stable_proposal_key(job.url, job.external_id, job.title, job.client_key, profile.id)
            db.audit('proposal_generated', job_id=job.id, details={'validation_status': p.validation_status, 'price': p.suggested_price})
            within_limits=db.send_gate(max_per_hour, max_per_day)[0]
            client_allowed = db.client_send_gate(job.client_key)[0]
            decision=policy.decide(provider,analysis,p,sender,db.already_sent(key),within_limits and client_allowed)
            db.save_proposal(p,key,'READY_TO_SEND' if decision=='AUTO_SEND' else 'REVIEW_REQUIRED',
                             project_type=job.category or job.title, client_key=job.client_key)
            if decision=='AUTO_SEND':
                if not db.claim_send(key):
                    stats['review']+=1
                    db.audit('proposal_send_claim_blocked', job_id=job.id)
                    continue
                receipt=sender.send(p)
                external_id=receipt.get('external_id')
                if not external_id: raise PipelineError('sender returned no external_id')
                db.mark_sent(key,external_id); db.consume_limit('send-hour'); db.consume_limit('send-day'); stats['sent']+=1
                db.audit('proposal_sent', job_id=job.id, details={'external_id': external_id, 'price': p.suggested_price})
            else: stats['review']+=1
        except Exception as exc:
            stats['errors']+=1
            db.audit('pipeline_error',job_id=job.id,details={'provider':provider.name,'error':str(exc)})
    return stats

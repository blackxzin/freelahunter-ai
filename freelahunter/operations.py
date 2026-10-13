"""Local review queue, analysis cache and explicitly recorded sales outcomes."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ('draft', 'sent', 'responded', 'negotiating', 'won', 'lost', 'archived')


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def assess(snapshot, filters):
    text = f"{snapshot.get('title', '')} {snapshot.get('description', '')}".lower()
    matches = [term for term in filters['technologies'] if re.search(r'(?<!\w)' + re.escape(term.lower()) + r'(?!\w)', text)]
    reasons, exclusions = [], []
    if matches:
        reasons.append('Tecnologias compatíveis: ' + ', '.join(matches))
    elif filters['require_technology']:
        exclusions.append('Nenhuma tecnologia configurada foi identificada.')
    excluded = [word for word in filters['excluded_terms'] if word.lower() in text]
    if excluded:
        exclusions.append('Termos excluídos: ' + ', '.join(excluded))
    currency = snapshot.get('currency', 'BRL')
    minimum = filters['minimum_budget'].get(currency)
    budget = snapshot.get('budget_max')
    if budget is None:
        reasons.append('Orçamento não informado: confirmar com o cliente.')
    elif minimum is not None and budget < minimum:
        exclusions.append(f'Orçamento {currency} {budget:g} abaixo do mínimo {minimum:g}.')
    else:
        reasons.append(f'Orçamento informado: {currency} {budget:g}.')
    description = snapshot.get('description', '')
    clarity = sum(bool(re.search(pattern, text)) for pattern in (
        r'entreg|deliver|implementar|build|desenvolver', r'aceite|acceptance|test|layout aprovado',
        r'fornecid|fornecemos|provided|acessos|documenta', r'prazo|deadline|dias|weeks|semanas',
    ))
    if len(description.strip()) < filters['minimum_description_length']:
        exclusions.append('Descrição curta demais para avaliar o projeto.')
    reasons.append(f'Clareza do escopo: {clarity}/4 sinais (entrega, aceite, materiais e prazo).')
    score = min(100, 25 + min(len(matches), 4) * 10 + clarity * 7 + (7 if budget else 0))
    if score < filters['minimum_score']:
        exclusions.append(f'Pontuação {score} abaixo do mínimo {filters["minimum_score"]}.')
    return {'score': score, 'eligible': not exclusions, 'reasons': reasons, 'exclusions': exclusions, 'matched_technologies': matches}


class OperationsStore:
    def __init__(self, root):
        self.root = Path(root)
        directory = self.root / 'state'
        directory.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(directory / 'operations.db', timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS opportunities (
          id TEXT PRIMARY KEY, platform TEXT NOT NULL, url TEXT NOT NULL,
          snapshot TEXT NOT NULL, analysis_key TEXT NOT NULL, draft TEXT NOT NULL,
          selection TEXT NOT NULL, edited_message TEXT, outcome TEXT NOT NULL DEFAULT 'draft',
          notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS opportunity_events (
          id INTEGER PRIMARY KEY, opportunity_id TEXT NOT NULL, event TEXT NOT NULL,
          details TEXT NOT NULL, created_at TEXT NOT NULL
        );''')

    def close(self):
        self.conn.close()

    def filters(self):
        return json.loads((self.root / 'config/hunt_filters.json').read_text())

    def analysis_key(self, snapshot):
        files = list((self.root / 'config').glob('*.json')) + list((self.root / 'config').glob('*.yaml')) + list((self.root / 'knowledge').glob('*'))
        files += [self.root / 'freelahunter' / file for file in ('quoting.py', 'core.py', 'knowledge.py', 'operations.py')]
        versions = [(str(p.relative_to(self.root)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files) if p.is_file()]
        return fingerprint({'snapshot': snapshot, 'files': versions})

    def cached(self, snapshot, key):
        row = self.conn.execute('SELECT * FROM opportunities WHERE id=? AND analysis_key=?', (fingerprint(snapshot['url']), key)).fetchone()
        if row:
            self.conn.execute('UPDATE opportunities SET checked_at=? WHERE id=?', (now(), row['id']))
            self.conn.commit()
            return self.decode(row)
        return None

    @staticmethod
    def decode(row):
        data = dict(row)
        for field in ('snapshot', 'draft', 'selection'):
            data[field] = json.loads(data[field])
        draft = data['draft']
        generated = draft.get('question', '') if draft.get('action') == 'question' else draft.get('message', '')
        data['generated_message'] = generated
        data['message'] = data['edited_message'] if data['edited_message'] is not None else generated
        return data

    def save(self, snapshot, key, draft, selection):
        identifier = fingerprint(snapshot['url'])
        stamp = now()
        with self.conn:
            self.conn.execute('''INSERT INTO opportunities
              (id,platform,url,snapshot,analysis_key,draft,selection,created_at,updated_at,checked_at)
              VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              snapshot=excluded.snapshot,analysis_key=excluded.analysis_key,draft=excluded.draft,
              selection=excluded.selection,updated_at=excluded.updated_at,checked_at=excluded.checked_at,
              revision=opportunities.revision+1''',
              (identifier, snapshot.get('platform', '99freelas'), snapshot['url'], json.dumps(snapshot), key,
               json.dumps(draft), json.dumps(selection), stamp, stamp, stamp))
            self.conn.execute('INSERT INTO opportunity_events(opportunity_id,event,details,created_at) VALUES(?,?,?,?)',
                              (identifier, 'analyzed', json.dumps({'score': selection['score']}), stamp))
        return self.get(identifier)

    def get(self, identifier):
        row = self.conn.execute('SELECT * FROM opportunities WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Rascunho não encontrado.')
        data = self.decode(row)
        data['events'] = [dict(r) for r in self.conn.execute('SELECT event,details,created_at FROM opportunity_events WHERE opportunity_id=? ORDER BY id DESC LIMIT 50', (identifier,))]
        return data

    def listing(self):
        rows = [self.decode(row) for row in self.conn.execute('SELECT * FROM opportunities ORDER BY updated_at DESC')]
        counts = {status: sum(row['outcome'] == status for row in rows) for status in STATUSES}
        contacted = sum(counts[s] for s in ('sent', 'responded', 'negotiating', 'won', 'lost'))
        counts['conversion_rate'] = counts['won'] / contacted if contacted else 0
        return {'items': rows, 'summary': counts}

    def update(self, identifier, payload):
        if not isinstance(payload, dict) or set(payload) - {'revision', 'message', 'outcome', 'notes'}:
            raise ValueError('Campos inválidos.')
        if type(payload.get('revision')) is not int:
            raise ValueError('Versão do rascunho obrigatória.')
        for field in ('message', 'notes'):
            if field in payload and (not isinstance(payload[field], str) or len(payload[field]) > 20000):
                raise ValueError(f'{field}: texto inválido ou muito longo.')
        if 'message' in payload and not payload['message'].strip():
            raise ValueError('A proposta não pode ficar vazia.')
        if 'outcome' in payload and payload['outcome'] not in STATUSES:
            raise ValueError('Status inválido.')
        with self.conn:
            row = self.get(identifier)
            result = self.conn.execute('''UPDATE opportunities SET edited_message=?,outcome=?,notes=?,
              revision=revision+1,updated_at=? WHERE id=? AND revision=?''',
              (payload.get('message', row['edited_message']), payload.get('outcome', row['outcome']),
               payload.get('notes', row['notes']), now(), identifier, payload['revision']))
            if not result.rowcount:
                raise RuntimeError('O rascunho mudou. Atualize a lista antes de salvar.')
            self.conn.execute('INSERT INTO opportunity_events(opportunity_id,event,details,created_at) VALUES(?,?,?,?)',
                              (identifier, 'reviewed', json.dumps({'outcome': payload.get('outcome', row['outcome']), 'message_edited': 'message' in payload}), now()))
        return self.get(identifier)

    def import_drafts(self):
        for path in (self.root / 'state/drafts').glob('*/*.json'):
            try:
                data = json.loads(path.read_text())
                snapshot, draft = data['snapshot'], data['draft']
                identifier = fingerprint(snapshot['url'])
                if self.conn.execute('SELECT 1 FROM opportunities WHERE id=?', (identifier,)).fetchone():
                    continue
                self.save(snapshot, '', draft, assess(snapshot, self.filters()))
            except (ValueError, KeyError, TypeError):
                continue

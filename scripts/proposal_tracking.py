#!/usr/bin/env python3
"""Manual and browser-bridge operations for proposal outcomes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from freelahunter.core import Database, normalize_client_key, sha, stable_proposal_key


def database(args):
    return Database(args.database)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str))


def register(args):
    payload = json.load(sys.stdin)
    db = database(args)
    client_key = normalize_client_key(payload.get('client_key'))
    explicit_key = payload.get('idempotency_key')
    key = explicit_key or stable_proposal_key(
        payload.get('url'), payload.get('external_id'), payload.get('title'), client_key,
        payload.get('profile_id', 'default'),
    )
    # Keep compatibility with records created by the old message-based key.
    # Once found, continue using that key so a previously sent item cannot be
    # re-submitted merely because its generated copy changed.
    legacy_key = sha('|'.join(str(payload.get(k, '')) for k in ('url', 'title', 'message')))
    existing = db.conn.execute('SELECT status FROM proposals WHERE idempotency_key=?', (key,)).fetchone()
    if not explicit_key and not existing and legacy_key != key:
        legacy = db.conn.execute('SELECT status FROM proposals WHERE idempotency_key=?', (legacy_key,)).fetchone()
        if legacy:
            key, existing = legacy_key, legacy
    send_kind = payload.get('send_kind', 'proposal')
    proposal_id = db.register_proposal(
        payload.get('job_id'), key, payload.get('subject') or f"Proposta: {payload.get('title', '')}",
        payload.get('message', ''), payload.get('price'), payload.get('project_type'),
        client_key, payload.get('conversation_id'), payload.get('validation_status', 'PENDING'), send_kind,
    )
    emit({'proposal_id': proposal_id, 'idempotency_key': key,
          'send_kind': send_kind,
          'already_sent': bool(existing and existing[0] in {'SENT', 'SENDING'})})


def mark_sent(args):
    db = database(args)
    row = db.conn.execute('SELECT idempotency_key, status, send_kind FROM proposals WHERE id=?', (args.proposal_id,)).fetchone()
    if not row:
        raise SystemExit(f'proposta não encontrada: {args.proposal_id}')
    marked = db.mark_sent(row[0], args.external_id or f'manual-{args.proposal_id}', args.conversation_id)
    # The browser bridge is an external sender, so it must consume the same
    # rolling-window counters used by the native pipeline.  Do this only on
    # the first transition to SENT to keep retries idempotent.
    if marked and row[2] != 'message':
        db.consume_limit('send-hour')
        db.consume_limit('send-day')
    current = db.conn.execute('SELECT status FROM proposals WHERE id=?', (args.proposal_id,)).fetchone()
    emit({'proposal_id': args.proposal_id, 'status': current[0] if current else None, 'marked': marked})


def claim(args):
    db = database(args)
    row = db.conn.execute('SELECT idempotency_key FROM proposals WHERE id=?', (args.proposal_id,)).fetchone()
    if not row:
        raise SystemExit(f'proposta não encontrada: {args.proposal_id}')
    emit({'proposal_id': args.proposal_id,
          'claimed': db.claim_send(row[0], args.max_per_client, 86400)})


def set_status(args):
    db = database(args)
    db.set_proposal_outcome(args.proposal_id, args.status, args.reason)
    emit({'proposal_id': args.proposal_id, 'outcome_status': args.status})


def add_message(args):
    db = database(args)
    db.record_message(args.conversation_id, args.text, args.direction, args.proposal_id,
                      args.job_id, is_scope_context=args.scope_context)
    emit({'conversation_id': args.conversation_id, 'saved': True})


def report(args):
    db = database(args)
    report_data = db.conversion_report()
    summary, groups = report_data['summary'], report_data['groups']
    total, responded, accepted, rejected, avg_days = summary
    emit({'sent': total or 0, 'responded': responded or 0, 'accepted': accepted or 0,
          'rejected': rejected or 0, 'response_rate': (responded / total if total else 0),
          'conversion_rate': (accepted / total if total else 0), 'avg_days_to_response': avg_days,
          'groups': [list(row) for row in groups]})


def gate(args):
    db = database(args)
    db.evaluate_pause(args.no_response_days, args.consecutive_no_response, args.rejection_threshold)
    allowed, reason = db.send_gate(args.max_per_hour, args.max_per_day)
    emit({'allowed': allowed, 'reason': reason})
    if not allowed:
        raise SystemExit(2)


def client_gate(args):
    payload = json.load(sys.stdin)
    allowed, reason = database(args).client_send_gate(
        payload.get('client_key'), int(payload.get('max_per_day', 1)), 86400,
    )
    emit({'allowed': allowed, 'reason': reason})


def message_gate(args):
    payload = json.load(sys.stdin)
    allowed, reason = database(args).client_message_gate(
        payload.get('client_key'), int(payload.get('max_per_day', 1)), 86400,
    )
    emit({'allowed': allowed, 'reason': reason})


def pause(args):
    db = database(args)
    db.set_automation_pause(args.action == 'on', args.reason)
    emit({'paused': args.action == 'on', 'reason': args.reason})


def history(args):
    emit(database(args).client_history(args.client_key))


def context(args):
    emit({'messages': database(args).scope_context(args.client_key, args.conversation_id, args.limit)})


def main():
    sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Acompanha propostas e conversões do FreelaHunter.')
    parser.add_argument('--database', default=None)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('register'); p.set_defaults(func=register)
    p = sub.add_parser('claim'); p.add_argument('--proposal-id', type=int, required=True); p.add_argument('--max-per-client', type=int, default=1); p.set_defaults(func=claim)
    p = sub.add_parser('sent'); p.add_argument('--proposal-id', type=int, required=True); p.add_argument('--external-id'); p.add_argument('--conversation-id'); p.set_defaults(func=mark_sent)
    p = sub.add_parser('status'); p.add_argument('--proposal-id', type=int, required=True); p.add_argument('--set', dest='status', choices=sorted(Database.OUTCOME_STATUSES), required=True); p.add_argument('--reason'); p.set_defaults(func=set_status)
    p = sub.add_parser('message'); p.add_argument('--conversation-id', required=True); p.add_argument('--proposal-id', type=int); p.add_argument('--job-id', type=int); p.add_argument('--direction', choices=['inbound', 'outbound'], default='inbound'); p.add_argument('--scope-context', action='store_true'); p.add_argument('--text', required=True); p.set_defaults(func=add_message)
    p = sub.add_parser('report'); p.set_defaults(func=report)
    p = sub.add_parser('gate'); p.add_argument('--max-per-hour', type=int, default=3); p.add_argument('--max-per-day', type=int, default=10); p.add_argument('--no-response-days', type=int, default=7); p.add_argument('--consecutive-no-response', type=int, default=5); p.add_argument('--rejection-threshold', type=float, default=.6); p.set_defaults(func=gate)
    p = sub.add_parser('client-gate'); p.set_defaults(func=client_gate)
    p = sub.add_parser('message-gate'); p.set_defaults(func=message_gate)
    p = sub.add_parser('pause'); p.add_argument('action', choices=['on', 'off']); p.add_argument('--reason'); p.set_defaults(func=pause)
    p = sub.add_parser('history'); p.add_argument('--client-key', required=True); p.set_defaults(func=history)
    p = sub.add_parser('context'); p.add_argument('--client-key'); p.add_argument('--conversation-id'); p.add_argument('--limit', type=int, default=20); p.set_defaults(func=context)
    args = parser.parse_args(); args.func(args)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Generate a self-contained, read-only HTML dashboard from SQLite."""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freelahunter.core import Database


def bar_rows(rows, label='count'):
    values = [row[1] for row in rows] or [0]
    maximum = max(values) or 1
    return ''.join(
        f'<tr><td>{html.escape(str(row[0]))}</td><td><div class="bar" style="width:{row[1] / maximum * 100:.1f}%">{row[1]}</div></td></tr>'
        for row in rows
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default=None)
    parser.add_argument('--output', default='reports/dashboard.html')
    args = parser.parse_args()
    db = Database(args.database)
    summary = db.conversion_report()['summary']
    sent, responded, accepted, rejected, avg_days = summary
    sent = sent or 0; responded = responded or 0; accepted = accepted or 0; rejected = rejected or 0
    response_rate = responded / sent if sent else 0
    conversion_rate = accepted / sent if sent else 0
    days = db.conn.execute('''SELECT substr(COALESCE(sent_at, first_seen_at),1,10), COUNT(*)
        FROM proposals LEFT JOIN jobs ON jobs.id=proposals.job_id GROUP BY 1 ORDER BY 1''').fetchall()
    jobs = db.conn.execute('SELECT substr(first_seen_at,1,10), COUNT(*) FROM jobs GROUP BY 1 ORDER BY 1').fetchall()
    groups = db.conversion_report()['groups']
    group_rows = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(value))}</td>' for value in row) + '</tr>' for row in groups)
    document = f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>FreelaHunter Dashboard</title>
    <style>body{{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 16px;color:#202124}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:10px;padding:16px;min-width:150px}}.value{{font-size:28px;font-weight:700}}table{{border-collapse:collapse;width:100%;margin:12px 0 28px}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}.bar{{background:#5865f2;color:#fff;padding:4px 8px;border-radius:4px;min-width:24px}}</style>
    <h1>FreelaHunter</h1><p>Gerado localmente a partir do SQLite.</p>
    <div class="cards"><div class="card">Enviadas<div class="value">{sent}</div></div><div class="card">Respostas<div class="value">{responded}</div></div><div class="card">Taxa resposta<div class="value">{response_rate:.1%}</div></div><div class="card">Aceitas<div class="value">{accepted}</div></div><div class="card">Conversão<div class="value">{conversion_rate:.1%}</div></div><div class="card">Dias até resposta<div class="value">{(avg_days or 0):.1f}</div></div></div>
    <h2>Vagas por dia</h2><table><tr><th>Dia</th><th>Quantidade</th></tr>{bar_rows(jobs)}</table>
    <h2>Propostas por dia</h2><table><tr><th>Dia</th><th>Quantidade</th></tr>{bar_rows(days)}</table>
    <h2>Conversão por tipo e preço</h2><table><tr><th>Tipo</th><th>Faixa</th><th>Enviadas</th><th>Respondidas</th><th>Aceitas</th><th>Recusadas</th></tr>{group_rows}</table>
    </html>'''
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(document, encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()

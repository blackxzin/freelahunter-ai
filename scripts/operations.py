"""Small JSON bridge between the browser hunter and its local review queue."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freelahunter.operations import OperationsStore, fingerprint, now, order_links, type_response_rates

store = OperationsStore(ROOT)
try:
    payload = json.load(sys.stdin)
    if sys.argv[1] == 'order':
        checked = {row['url']: row['checked_at'] for row in store.conn.execute('SELECT url,checked_at FROM opportunities')}
        print(json.dumps(order_links(payload['links'], checked, type_response_rates(ROOT))))
    elif sys.argv[1] == 'sent':
        identifier = fingerprint(payload['url'])
        with store.conn:
            store.conn.execute("UPDATE opportunities SET outcome='sent',revision=revision+1,updated_at=? WHERE id=? AND outcome='draft'", (now(), identifier))
            store.conn.execute('INSERT INTO opportunity_events(opportunity_id,event,details,created_at) VALUES(?,?,?,?)', (identifier, 'sent_confirmed', '{}', now()))
        print(json.dumps({'saved': True}))
    else:
        raise ValueError('Operação desconhecida.')
finally:
    store.close()

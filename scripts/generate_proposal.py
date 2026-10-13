"""Generate one proposal/question from a job snapshot on stdin."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from freelahunter.core import ProfileService, ProposalDraft, ProposalValidator
from freelahunter.quoting import build_quote
from freelahunter.operations import OperationsStore, assess


def main() -> None:
    # Node pipes UTF-8; Windows would otherwise decode it with the ANSI codepage.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, 'reconfigure'):  # absent on StringIO in tests
            stream.reconfigure(encoding='utf-8')
    snapshot = json.load(sys.stdin)
    root = Path(__file__).resolve().parents[1]
    platform = str(snapshot.get('platform') or '').lower()
    platforms = json.loads((root / 'config/platforms.json').read_text())
    config = platforms[platform or '99freelas']
    profile_path = root / config['profile_path']
    profile = ProfileService(str(profile_path)).load()
    profile.locale = config['locale']
    profile.currency = config['currency']
    profile.pricing_path = config['pricing_path']
    store = OperationsStore(root) if os.environ.get('HUNT_ANALYSIS_CACHE') == 'true' and snapshot.get('url') else None
    key = store.analysis_key(snapshot) if store else None
    cached = store.cached(snapshot, key) if store else None
    if cached:
        result = cached['draft']
        result.update(cache_hit=True, selection=cached['selection'], opportunity_id=cached['id'], outcome=cached['outcome'], reviewed=cached['edited_message'] is not None)
        store.close()
        print(json.dumps(result, ensure_ascii=False))
        return
    filters = json.loads((root / 'config/hunt_filters.json').read_text())
    selection = assess(snapshot, filters)
    result = build_quote(snapshot, profile) if selection['eligible'] or not store else {
        'action': 'skip', 'reason': '; '.join(selection['exclusions'])
    }
    if result.get('action') == 'proposal':
        draft = ProposalDraft(0, result.get('subject', ''), result.get('message') if result.get('action') == 'proposal' else result.get('question', ''), result.get('estimated_hours') or 0, result.get('suggested_price'), result.get('questions', []), currency=result.get('currency'))
        status, reasons = ProposalValidator().validate(draft, profile)
        result['validation_status'] = status
        result['validation_reasons'] = reasons
    result['selection'] = selection
    if store:
        saved = store.save(snapshot, key, result, selection)
        result.update(cache_hit=False, opportunity_id=saved['id'], outcome=saved['outcome'], reviewed=saved['edited_message'] is not None)
        store.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

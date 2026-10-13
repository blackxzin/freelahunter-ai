"""Isolated stdlib panel fixture; never reads the user's drafts or sends proposals."""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freelahunter.operations import OperationsStore, assess
import freelahunter.operator_panel as panel

with tempfile.TemporaryDirectory(prefix='freelahunter-panel-test-') as directory:
    root = Path(directory)
    shutil.copytree(ROOT / 'config', root / 'config')
    store = OperationsStore(root)
    snapshot = {'url': 'https://www.99freelas.com.br/project/test-123', 'platform': '99freelas',
                'title': 'API Python <script>alert(1)</script>', 'description': 'Desenvolver API Python com testes, documentação fornecida e prazo de 10 dias.',
                'currency': 'BRL', 'budget_max': 3000}
    store.save(snapshot, store.analysis_key(snapshot), {'action': 'proposal', 'message': 'Olá! Vamos desenvolver sua API.'}, assess(snapshot, store.filters()))
    store.close()
    panel.create_operator_app = lambda controller: None
    panel.serve_operator_panel(port=int(sys.argv[1]), manager=panel.HunterProcessManager(root))

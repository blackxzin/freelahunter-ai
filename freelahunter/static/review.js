(() => {
  const $ = id => document.getElementById(id);
  const labels = {draft:'Rascunho',sent:'Enviada',responded:'Respondida',negotiating:'Em negociação',won:'Trabalho fechado',lost:'Não fechado',archived:'Arquivada'};
  let items = [], active = null, dirty = false;
  const feedback = message => { $('review-feedback').textContent = message; };
  for (const [value,text] of Object.entries(labels)) $('draft-outcome').add(new Option(text,value));
  const fields = ['draft-message','draft-notes','draft-outcome'];
  fields.forEach(id => $(id).addEventListener('input', () => { dirty = true; }));
  function draw() {
    $('review-list').replaceChildren();
    const visible = items.filter(row => (!$('review-platform').value || row.platform === $('review-platform').value)
      && (!$('review-outcome').value || row.outcome === $('review-outcome').value)
      && (!$('review-eligible').checked || row.selection.eligible)
      && `${row.snapshot.title} ${row.snapshot.description}`.toLowerCase().includes($('review-query').value.toLowerCase()));
    visible.sort((a,b) => b.selection.score - a.selection.score);
    for (const row of visible) {
      const button = document.createElement('button');
      button.className = 'review-item'; button.setAttribute('aria-pressed', String(active?.id === row.id));
      button.textContent = `${row.snapshot.title} · ${row.platform} · ${row.selection.score}/100 · ${labels[row.outcome]}`;
      button.onclick = () => open(row.id); $('review-list').append(button);
    }
    if (!visible.length) $('review-list').textContent = 'Nenhuma oportunidade com estes filtros. Inicie uma caça ou ajuste os filtros.';
  }
  async function request(url, options) {
    const response = await fetch(url, options); const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Falha ao carregar dados.');
    return data;
  }
  async function open(id) {
    if (dirty && !confirm('Descartar a revisão ainda não salva?')) return;
    try {
      active = await request('/api/review/' + id); dirty = false;
      $('review-editor').hidden = false; $('draft-title').textContent = active.snapshot.title;
      const url = new URL(active.url);
      $('draft-link').hidden = url.protocol !== 'https:';
      if (url.protocol === 'https:') $('draft-link').href = url.href;
      $('draft-score').textContent = `Compatibilidade: ${active.selection.score}/100 · ${active.snapshot.currency || ''} · ${active.snapshot.budget_text || 'Orçamento não informado'}`;
      $('draft-reasons').replaceChildren();
      for (const reason of [...active.selection.reasons,...active.selection.exclusions]) { const li=document.createElement('li');li.textContent=reason;$('draft-reasons').append(li); }
      $('draft-description').textContent = active.snapshot.description;
      $('draft-message').value = active.message; $('draft-notes').value=active.notes; $('draft-outcome').value=active.outcome;
      $('draft-events').replaceChildren();
      for (const event of active.events || []) { const li=document.createElement('li');li.textContent=`${new Date(event.created_at).toLocaleString()} · ${event.event === 'analyzed' ? 'Análise gerada' : event.event === 'sent_confirmed' ? 'Envio confirmado' : 'Revisão registrada'} · ${event.details}`;$('draft-events').append(li); }
      draw();
    } catch (error) { feedback(error.message); }
  }
  async function reload() {
    try {
      const data = await request('/api/review'); items=data.items;
      $('review-summary').textContent = `${items.length} oportunidades · ${data.summary.sent} enviadas · ${data.summary.responded} respondidas · ${data.summary.negotiating} em negociação · ${data.summary.won} fechadas · conversão ${(data.summary.conversion_rate*100).toFixed(0)}%`;
      $('legacy-summary').textContent = `${data.legacy.length} registros anteriores recentes (separados da fila de revisão).`;
      $('legacy-list').replaceChildren();
      for (const row of data.legacy) { const li=document.createElement('li');li.textContent=`${row.subject} · ${row.status} · ${row.outcome_status}`;$('legacy-list').append(li); }
      draw();
    } catch(error) { feedback(error.message); }
  }
  $('save-draft').onclick = async () => {
    if (!active) return;
    $('save-draft').disabled=true;
    try {
      const data = await request('/api/review/' + active.id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:active.revision,...($('draft-message').value.trim() ? {message:$('draft-message').value} : {}),notes:$('draft-notes').value,outcome:$('draft-outcome').value})});
      active=data;dirty=false;feedback('Revisão salva. Nenhuma mensagem foi enviada.');await reload();
    } catch(error) { feedback(error.message); }
    finally { $('save-draft').disabled=false; }
  };
  $('copy-draft').onclick = async () => {
    try { await navigator.clipboard.writeText($('draft-message').value); feedback('Texto copiado.'); }
    catch { $('draft-message').focus();$('draft-message').select();feedback('Selecionei o texto. Use Ctrl+C para copiar.'); }
  };
  $('reload-review').onclick = reload;
  ['review-platform','review-outcome','review-query','review-eligible'].forEach(id => $(id).addEventListener('input',draw));
  window.addEventListener('beforeunload', event => { if(dirty) {event.preventDefault();event.returnValue='';} });
  reload();
})();

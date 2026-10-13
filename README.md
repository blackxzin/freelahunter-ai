# FreelaHunter AI

Plataforma segura de descoberta, análise e geração de propostas para freelancing. O bootstrap inclui providers mock, normalização, deduplicação por conteúdo, filtros baratos, análise determinística substituível por AI provider, matching de portfólio, validação, preço, política de envio, SQLite e worker.

## Executar

```bash
python -m pip install -r requirements-dev.txt   # pyyaml, pytest, fastapi/httpx
python scripts/demo.py
python scripts/hunt.py --provider mock
python scripts/e2e.py
python scripts/worker.py
python -m pytest
npm install
npm run test:browser
```

### Arch Linux

O Arch bloqueia `pip install` no Python do sistema (PEP 668) e o
`playwright install --with-deps` só conhece apt; use venv e as dependências do pacman:

```bash
sudo pacman -S --needed python nodejs npm nss alsa-lib at-spi2-core libxkbcommon libxcomposite libxdamage libxrandr mesa
python -m venv .venv && source .venv/bin/activate   # os scripts Node chamam `python`: deixe o venv ativo
python -m pip install -r requirements-dev.txt
npm install && npx playwright install chromium        # sem --with-deps
```

Caça assistida no Chromium (login manual):

```bash
MAX_JOBS=5 node scripts/interactive_hunt.mjs

# 99Freelas contínuo e automático: caça a próxima vaga assim que termina a anterior
npm run hunt:99:auto

# Upwork: duas vagas por ciclo, com intervalo de 12 minutos
PLATFORM=upwork MAX_JOBS=2 HUNT_INTERVAL_MINUTES=12 RUN_FOREVER=true \
  AUTO_SEND=false DRY_RUN=true node scripts/interactive_hunt.mjs
# equivalente multiplataforma (Windows/PowerShell)
npm run hunt:upwork
```

Painel local para escolher qual plataforma caçar:

```bash
python scripts/operator_panel.py
# abra http://127.0.0.1:8765
```

O painel permite selecionar 99Freelas, Upwork ou Workana, iniciar/parar uma única caça
por vez e enviar Enter ao processo depois de login, Google, Cloudflare ou outro
desafio manual. 99Freelas usa o ciclo normal de 5 vagas a cada 15 minutos;
Upwork usa 2 vagas a cada 12 minutos. Por padrão, o painel inicia ambos em
`SEMI_AUTO`, `DRY_RUN=true` e `AUTO_SEND=false`, preparando rascunhos sem envio.
Com `PANEL_AUTOMATION_MODE=AUTO`, o 99Freelas roda continuamente e envia após
passar todas as travas. O painel escuta somente em localhost.

O navegador abre, usa uma sessão autenticada salva, ignora Premium/bandeira
dourada, lê a vaga, preenche proposta ou pergunta e calcula valor/prazo.
No modo `SEMI_AUTO`, pede `ENVIAR`; no modo `AUTO`, envia somente após todas as
travas configuráveis passarem. `BROWSER_PROFILE_DIR` pode apontar para um perfil
separado.

Monitor de respostas no Discord (sem responder clientes automaticamente):

```bash
npm run monitor:inbox
npm run smoke:selectors
python scripts/proposal_tracking.py report
python scripts/dashboard.py
```

Defina `DISCORD_WEBHOOK_URL` em `.env`. `INBOX_POLL_SECONDS` controla o intervalo
(mínimo de 20 segundos). O monitor usa a sessão manual do Chromium, alerta
somente conversa não lida do cliente e inclui link para responder dentro do
99Freelas. Falhas temporárias do Discord são repetidas com backoff, falhas de
leitura não encerram o monitor. O estado local impede alertas duplicados após
reiniciar.

Automação total (opt-in explícito): `AUTOMATION_MODE=AUTO`, `AUTO_SEND=true` e
`DRY_RUN=false`, com `AUTO_SEND_KILL_SWITCH=false`. Mesmo nesse modo continuam
valendo validação da proposta, limites por hora/dia, idempotência e circuito de
pausa. A reserva de envio é atômica por vaga e cliente; cópias recalculadas
mantêm a mesma chave estável. Quando o limite de propostas é atingido, o modo
AUTO pode enviar uma única mensagem fallback por cliente no período configurado,
sem consumir o limite de propostas, incluindo estimativa calculada e aviso de
que o preço pode ser negociado. O smoke test é somente leitura; use
`SMOKE_JOB_URL` para validar também os campos da página de uma vaga.

No caça Chromium, `MAX_PROPOSALS_PER_CLIENT_24H` e
`MAX_MESSAGES_PER_CLIENT_24H` evitam novo contato quando o cliente é identificado;
o segundo tem padrão 1. Sem identificador, a proteção continua valendo pela chave
estável da vaga. `AUTO_MESSAGE_ON_LIMIT=false` desativa o fallback.

No comando contínuo, `HUNT_INTERVAL_MINUTES=0` elimina a espera de minutos entre
ciclos. O bot ainda mantém uma pausa curta entre ações do navegador, consulta a
caixa de mensagens do 99Freelas antes de cada contato e bloqueia qualquer cliente
que já tenha uma conversa existente.

O painel local continua seguro por padrão. Para ativar o mesmo modo automático
contínuo pelo painel, inicie-o com `PANEL_AUTOMATION_MODE=AUTO`.

Para preparar rascunhos no Upwork, use a sessão manual do Chromium:

```bash
PLATFORM=upwork AUTO_SEND=false DRY_RUN=true node scripts/interactive_hunt.mjs
```

O modo Upwork é somente leitura/rascunho enquanto os seletores reais não forem
validados. Se aparecer Cloudflare ou CAPTCHA, resolva o desafio manualmente no
Chromium e pressione Enter no terminal; o processo retoma a leitura depois que a
página normal voltar. No Upwork, o perfil e as propostas são gerados em inglês,
apresentam explicitamente a dupla de desenvolvedores full-stack e usam a moeda
detectada no orçamento da vaga (`USD`, `EUR`, `GBP` ou `BRL`). A taxa e as faixas
por moeda ficam em `config/upwork_pricing.json`; são premissas configuráveis, não
conversão cambial em tempo real. Se a sessão estiver deslogada, o fluxo tenta
clicar em “Continue with Google” e selecionar a conta visível `Lucas`; senha,
2FA, CAPTCHA e recuperação de conta continuam manuais.

API opcional:

```bash
python -m pip install -r requirements-api.txt
ADMIN_TOKEN='defina-um-token-forte' uvicorn freelahunter.api:create_app --factory --host 127.0.0.1 --port 8000
```

O provider HTTP (`HttpJobProvider`) aceita apenas feeds JSON via HTTPS e somente
consulta vagas. Envio continua separado, protegido por `DRY_RUN`, kill switch,
provider autorizado e aprovação explícita.

## Perfil Upwork

O conteúdo revisável do perfil está em `config/upwork_profile.yaml`; os campos
também são refletidos no perfil local em `config/profile.yaml`. Ele foi escrito
em inglês para a apresentação no Upwork e descreve somente o trabalho e as
capacidades comprovadas neste repositório. A cópia deve ser conferida e
preenchida manualmente na conta em
`https://www.upwork.com/freelancers/~0173a99e2479ab2b2e`. Não há automação de
login, CAPTCHA ou salvamento automático de alterações de perfil. A base de
conhecimento editorial da dupla está em
`knowledge/team-devs-senior-full-stack.md`; campos entre colchetes precisam ser
preenchidos antes de virar informação pública.

## Estimativas e qualidade das propostas

O fluxo Chromium usa `freelahunter/quoting.py` para decompor entregas e gerar
faixas preliminares de horas, valores e dias corridos. Configure taxa horária,
capacidade da equipe, testes e reserva em `config/pricing.json`. Essas premissas
não são uma média de mercado. Consulte `docs/PRICING.md` para fórmulas e limites.
Escopos insuficientes geram perguntas, sem preencher preço/prazo fechado.
Referências comerciais fornecidas pelo usuário ficam em
`knowledge/freelance-market-br-2026.json`. Elas servem como sanity check e
orientação de proposta; nunca substituem o cálculo por escopo e horas.
Os seletores reais do 99Freelas ainda precisam de validação; os testes locais
não comprovam funcionamento completo na conta real.

Defaults são `DRY_RUN=true`, `AUTO_SEND=false`, `AUTO_SEND_KILL_SWITCH=true`. Somente `AuthorizedMockProvider` declara envio autorizado. Integrações reais devem implementar `JobProvider`/`ProposalSender` sem bypass de CAPTCHA, Cloudflare ou rate limits.

Arquitetura detalhada: `docs/ARCHITECTURE.md`, `docs/AUTOMATION.md`, `docs/SECURITY.md`.

## Navegador

`freelahunter.browser_automation.ProposalBrowserAutomation` aceita apenas URLs HTTPS e preenche somente propostas validadas. Submissão exige aprovação explícita e provider autorizado. Adaptadores de Chrome/Playwright devem respeitar termos da plataforma, CAPTCHA e rate limits; testes não usam login, credenciais ou envio externo.

## Workana

Execute `npm run hunt:workana` ou selecione **Workana** no painel local.
São até 5 projetos de TI e programação em português por ciclo, a cada 15 minutos.
Login e desafios de acesso são resolvidos manualmente no Chromium.

A integração gera perguntas/propostas e estimativas, salvando o resultado em
`state/drafts/workana/*.json` com URL, descrição, moeda, texto e orçamento.
As premissas de preço ficam em `config/workana_pricing.json`; não são cotações
cambiais nem uma garantia das taxas cobradas pela plataforma.

**Estado da integração:** leitura e rascunhos implementados; seletores ainda
precisam ser conferidos com uma sessão real. A consulta pública apresentou
Cloudflare. Envio, preenchimento do formulário e monitoramento da caixa de
mensagens da Workana não estão habilitados. Para conferir os seletores após
login, feche o navegador que usa o mesmo perfil e rode `npm run smoke:workana`.
Use `SMOKE_JOB_URL` para indicar um projeto específico.

Em todas as plataformas, `DRY_RUN=true` ou `AUTO_SEND_KILL_SWITCH=true`
bloqueiam o caminho de envio, inclusive manual. Rascunhos locais não dependem
dos limites de envio e ficam em `state/drafts/<plataforma>/`.

## Revisão, seleção e acompanhamento

Inicie `python scripts/operator_panel.py` e abra `http://127.0.0.1:8765`.
A seção **Rascunhos e resultados** permite buscar oportunidades, filtrar por
plataforma/etapa, revisar o texto, salvar anotações e copiar a proposta.
As oportunidades aparecem por pontuação, com os motivos de compatibilidade e
exclusão. Desmarque **Apenas vagas compatíveis** para examinar as descartadas.

As etapas são rascunho, enviada, respondida, em negociação, trabalho fechado,
não fechado e arquivada. Alterar a etapa registra uma informação manual; não
contata o cliente. Envios confirmados pelo fluxo do bot atualizam a fila para
**enviada**. Os registros antigos do SQLite aparecem em uma seção separada.
Respostas e negociações devem ser atualizadas pelo operador: a fila não faz
sincronização automática dessas etapas com as plataformas.

Configure tecnologias, termos excluídos, orçamento mínimo por moeda, tamanho
mínimo de descrição e pontuação mínima em `config/hunt_filters.json`.
A pontuação é uma regra explicável de compatibilidade e clareza, não uma
probabilidade de contratação. Orçamento ausente pede confirmação e não elimina
sozinho a oportunidade.

A fila e o histórico ficam em `state/operations.db`. Vagas sem mudanças
reutilizam a análise; alterações no conteúdo, perfil, preços, filtros ou motor
de análise invalidam o cache. O navegador ainda relê o projeto para detectar
mudanças. Vagas novas ou há mais tempo sem consulta têm prioridade. Textos
editados e etapas registradas são preservados mesmo após uma nova análise.
Uma revisão manual salva retira a oportunidade do caminho de envio automático;
a edição deve ser copiada e enviada manualmente após revisão.

Os comandos `npm run hunt:chrome`, `hunt:99:auto`, `hunt:upwork` e o painel usam
um supervisor que reinicia o navegador após desconexão, até 3 vezes por execução
(configurável com `BROWSER_RESTART_LIMIT`). Leituras com falhas temporárias de
rede ou HTTP 429/5xx têm até 3 tentativas com espera crescente. No modo contínuo,
a listagem indisponível é consultada novamente após 60 segundos. Login expirado
pausa para ação manual e o botão **Continuar após login** retoma o processo.
Cliques de envio não são repetidos pelo mecanismo de recuperação.

A validação da Workana na conta real foi adiada; seu envio permanece desativado.

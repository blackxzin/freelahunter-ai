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

Caça assistida no Chromium (login manual):

```bash
MAX_JOBS=5 node scripts/interactive_hunt.mjs

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

O painel permite selecionar 99Freelas ou Upwork, iniciar/parar uma única caça
por vez e enviar Enter ao processo depois de login, Google, Cloudflare ou outro
desafio manual. 99Freelas usa o ciclo normal de 5 vagas a cada 15 minutos;
Upwork usa 2 vagas a cada 12 minutos. O painel sempre inicia ambos em
`SEMI_AUTO`, `DRY_RUN=true` e `AUTO_SEND=false`; portanto ele prepara rascunhos
e não envia propostas automaticamente. O painel escuta somente em localhost.

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

# Automation

Modos MANUAL, SEMI_AUTO e AUTO são suportados por configuração. No Chromium,
`AUTO` caça vagas, calcula preço, gera texto, preenche e envia após validação.
Envio automático exige `AUTO_SEND=true`, `DRY_RUN=false`, kill switch desligado,
validação aprovada, limites por hora/dia e circuito de pausa liberado. Defaults
continuam seguros: `DRY_RUN=true`, `AUTO_SEND=false` e kill switch ligado.

Antes de cada clique, o bot consulta o histórico local e reserva atomicamente a
vaga e o cliente. A chave não depende do texto gerado, então recalcular preço ou
mensagem não libera um reenvio. Quando o limite horário/diário de propostas acaba,
o modo AUTO pode trocar a proposta por uma mensagem com preço preliminar calculado
por escopo, sempre informando que o preço pode ser negociado. Mensagens não usam o
limite de propostas, mas há uma trava separada por cliente (`MAX_MESSAGES_PER_CLIENT_24H`,
padrão 1) para evitar spam.

No 99Freelas, antes de preparar ou enviar uma proposta, o modo Chromium consulta
a caixa de mensagens e compara o cliente por URL e nome normalizados. Qualquer
conversa existente com a mesma pessoa bloqueia novo contato, inclusive quando o
envio anterior foi feito manualmente e não está no banco local. A consulta percorre
as páginas disponíveis da caixa; se a caixa não puder ser verificada, o envio é
bloqueado.

## Navegador

`ProposalBrowserAutomation` só abre vaga HTTPS e preenche proposta com validação `PASSED`. O adaptador não realiza login, não contorna CAPTCHA/anti-bot e não envia nada sem aprovação explícita do usuário e autorização do provider. O teste Playwright usa exclusivamente formulário HTML mock local: `npm run test:browser`.

Recursos Premium, Pro, compra, assinatura e turbinar ficam bloqueados por padrão em `BrowserActionPolicy`. Projetos identificados como Premium não entram na lista de elegíveis.

## Caça assistida no Chromium

`node scripts/interactive_hunt.mjs` abre Chromium visível, usa sessão autenticada
salva, coleta links públicos da listagem, ignora marcadores Premium/bandeira
dourada e preenche os campos da vaga. Em `SEMI_AUTO`, o clique final exige
`ENVIAR`; em `AUTO`, as travas fazem essa decisão. O fluxo não contorna CAPTCHA,
não injeta credenciais e não usa API de IA externa.

Ativação explícita:

```bash
AUTOMATION_MODE=AUTO AUTO_SEND=true DRY_RUN=false \
AUTO_SEND_KILL_SWITCH=false node scripts/interactive_hunt.mjs
```

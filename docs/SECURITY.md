# Security

Segredos ficam em `.env` (nunca no banco, logs ou frontend). Não há automação de interfaces proibidas nem evasão de anti-bot. Comece sempre em dry-run/semi-auto e habilite produção explicitamente.

O modo `AUTO` é opt-in e mantém limites de 3 propostas por hora e 10 por dia,
idempotência, validação de conteúdo e kill switch. O circuito de pausa bloqueia
novos envios quando as propostas mais recentes (enviadas há mais de
`PAUSE_NO_RESPONSE_DAYS`) formam uma sequência de `PAUSE_CONSECUTIVE_NO_RESPONSE`
sem resposta, ou quando a taxa de rejeição das últimas 20 decisões passa de
`PAUSE_REJECTION_THRESHOLD`, desde que existam ao menos `PAUSE_MIN_DECIDED`
decisões. Uma resposta recente interrompe a sequência. O smoke test real é somente leitura.

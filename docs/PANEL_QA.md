# Validação do painel — 26/09/2026

Validação executada com uma fila de teste isolada, sem mensagens externas:

- Abrir proposta, editar texto e anotações, salvar e recuperar após recarregar.
- Copiar texto para a área de transferência.
- Registrar negociação e filtrar por etapa.
- Exibir conteúdo com marcação HTML como texto, sem executar scripts.
- Layout sem rolagem horizontal em 375, 768 e 1440 pixels.
- Nenhum erro JavaScript capturado durante o fluxo.
- API FastAPI: leitura, atualização, versão desatualizada e registro inexistente.
- Servidor da biblioteca padrão: exercitado pelo teste de navegador completo.
- Cache: conteúdo e filtros alterados invalidam a análise; revisões sobrevivem.
- Recuperação: espera crescente, limite de tentativas e erros permanentes.

Capturas locais em `test-results/panel-review-*.png` (não versionadas).
Sem baseline visual anterior: a comparação de regressão visual é inconclusiva.
Não foi feita uma auditoria completa de acessibilidade ou desempenho.
Validação real da Workana adiada a pedido do usuário.

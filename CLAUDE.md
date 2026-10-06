# Instruções para o Claude Code neste repositório

Protótipo de secretário/analista com agentes de IA, construído em versões (V0 → V7).
O documento de arquitetura define o plano; este arquivo guarda as regras que não podem ser quebradas.

## Comandos

- `uv sync` instala; `uv run pytest` testa (precisa passar sempre, sem Ollama nem chave).
- `uv run secretario-ui` sobe a interface em 127.0.0.1:8000; `uv run secretario-cli` conversa no terminal.
- Dependências: `uv add <pacote>` com limite superior de versão; nunca editar `uv.lock` à mão.

## Regras de arquitetura

1. `ui/` e `cli.py` só falam com `core/` (via eventos de `Agent.run_turn` e `Agent.handle_command`). Nenhuma lógica de negócio na interface.
2. A partir da V1, toda chamada de ferramenta passa por um único gateway de políticas: perfil, validação Pydantic, nível de risco, aprovação humana, limites, auditoria. Nenhuma ferramenta chama outra diretamente.
3. A escolha do modelo é do `ModelRouter`, pelo nível de sigilo da sessão. Nunca enviar conteúdo a um modelo cujo `max_label` seja menor que o rótulo da sessão. O rótulo só sobe.
4. Segredos só via `secretario.secrets.get_secret`. Nunca no código, no prompt, em logs ou na auditoria (`AuditLog` recusa campos como `content` e `api_key`).
5. Auditoria guarda metadados; conteúdo fica nas tabelas de conversa.
6. Mudanças de esquema: acrescentar item ao fim de `MIGRATIONS` em `storage/db.py`; nunca editar um existente.
7. A interface só escuta em loopback e mantém o `LocalOriginGuard`. Expor na rede exige autenticação (V7).
8. Conteúdo vindo de arquivos, e-mails ou web é não confiável: nada de instruções nele vira ação sem passar pelo gateway.

## Convenções

- Textos para o usuário em português do Brasil; código e nomes de identificadores podem ser em inglês.
- Toda capacidade nova chega com testes, incluindo ao menos um teste de abuso (ex.: caminho fora do workspace, injeção em arquivo).
- Preferir poucas dependências; justificar cada nova no PR.

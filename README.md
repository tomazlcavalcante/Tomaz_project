# Secretário — V0

Protótipo de assistente pessoal/analista com agentes de IA, local por padrão.
A V0 é a fundação: chat no navegador, modelo de linguagem trocável, roteamento
por nível de sigilo, histórico e auditoria em SQLite. Ferramentas entram na V1.

## O que a V0 faz e o que ainda não faz

| Faz | Ainda não faz (versões seguintes) |
| --- | --- |
| Chat no navegador (Chainlit) e no terminal | Usar ferramentas (V1) |
| Modelo local via Ollama ou Gemini, trocável por configuração | Memória de longo prazo (V2) |
| Nível de sigilo por conversa, que só sobe e decide qual modelo pode ser usado | Ler documentos e anexos (V3) |
| Histórico e trilha de auditoria em `data/secretario.db` | Análise de dados e Python (V4) |
| Chaves de API no Gerenciador de Credenciais do Windows | E-mail, agenda, contatos (V5+) |

## Instalação no Windows (uma vez)

Todos os comandos abaixo são para o PowerShell.

**1. Instale o uv** (gerencia Python e dependências; não precisa instalar Python antes):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Feche e reabra o PowerShell depois.

**2. Instale o Ollama** pelo instalador em <https://ollama.com/download>.

**3. Configure o Ollama** e reinicie-o (ícone perto do relógio → Quit, depois abra de novo pelo menu Iniciar):

```powershell
setx OLLAMA_CONTEXT_LENGTH 8192   # janela de contexto; o padrão (4096) é pequeno
setx OLLAMA_NO_CLOUD 1            # desliga os modelos de nuvem do Ollama: nada sai da máquina por ele
```

**4. Baixe o modelo local** (cerca de 3,5 GB):

```powershell
ollama pull qwen3.5:4b
```

**5. (Opcional) Chave do Gemini.** Crie uma chave em <https://aistudio.google.com> ("Get API key") e guarde-a no cofre do Windows. Ela não aparece na tela nem fica em arquivo:

```powershell
uv run secretario-secrets set GEMINI_API_KEY
```

**6. Instale as dependências e rode os testes** (não precisam de Ollama nem de chave):

```powershell
cd caminho\para\secretario
uv sync
uv run pytest
```

## Uso

```powershell
uv run secretario-ui     # abre em http://127.0.0.1:8000
uv run secretario-cli    # a mesma conversa, no terminal
```

| Comando | O que faz |
| --- | --- |
| `/status` | Modelo preferido, modelo efetivo, nível de sigilo |
| `/modelos` | Lista os modelos configurados e o que cada um aceita |
| `/modelo gemini` | Troca o modelo preferido da conversa |
| `/sigilo confidencial` | Sobe o nível: `publico`, `interno`, `confidencial`, `restrito` |
| `/historico` | Conversas recentes |
| `/ajuda` | Lista de comandos |

Para comparar modelos, faça a mesma pergunta numa conversa com `/modelo local` e noutra com `/modelo gemini`. O rodapé de cada resposta mostra qual modelo respondeu, se foi local e quanto tempo levou.

## Privacidade: o que sai da sua máquina

- **Modelo local (Ollama):** nada sai. Com `OLLAMA_NO_CLOUD=1`, nem por engano.
- **Gemini no plano gratuito:** o texto da conversa vai para o Google, que pode usá-lo para melhorar os produtos dele, inclusive com leitura por revisores humanos. Por isso o perfil `gemini` tem `max_label = "publico"`: ao subir o sigilo para `interno` ou acima, a conversa passa automaticamente para o modelo local, mesmo que você tenha escolhido o Gemini. **Use o Gemini só com dados públicos ou sintéticos.**
- **Conversas e auditoria** ficam em `data/secretario.db`, fora do git. A auditoria guarda metadados (modelo, local ou externo, sigilo, tempos), nunca o conteúdo.

Para conferir o que foi para fora, abra o banco com `uv run python -m sqlite3 data\secretario.db` e rode:

```sql
SELECT ts, json_extract(data, '$.model') AS modelo, json_extract(data, '$.label') AS sigilo
FROM audit_events
WHERE kind = 'llm_call' AND json_extract(data, '$.is_local') = 0;
```

## Segurança já implementada

| Medida | Onde |
| --- | --- |
| A interface só aceita endereço de loopback (127.0.0.1/localhost); outro valor nem inicia | `config.py` |
| Bloqueio de acessos vindos de outros sites e de DNS rebinding (páginas abertas no navegador não conseguem conversar com o agente) | `ui/origin_guard.py` |
| Roteamento por sigilo decidido em código, a cada chamada, e auditado | `llm/router.py` |
| Modelo externo não aceita dados acima de "interno" sem liberação explícita | `config.py` |
| Modelos `:cloud` do Ollama são tratados como externos | `config.py` |
| Chaves no cofre do sistema, nunca no código, no prompt ou nos logs | `secrets.py`, `audit/log.py` |
| Camadas de dados externas do Chainlit desligadas; envio de arquivos desligado | `ui/launch.py`, `.chainlit/config.toml` |
| Versões exatas de todas as dependências | `uv.lock` |

## Desempenho esperado no seu laptop (i7-1165G7, 16 GB, sem GPU dedicada)

O Ollama deve rodar na CPU; confira com `ollama ps` (coluna PROCESSOR). A primeira resposta demora mais, porque o modelo é carregado na memória. O "pensamento" do modelo está desligado (`reasoning_effort = "none"`) para responder mais rápido.

- Lento demais: troque por `qwen3.5:2b` em `config/settings.toml`.
- Qualidade baixa: `qwen3.5:9b` (mais lento; descomente o exemplo `local9b`), ou o Gemini para dados públicos.

## Máquina Ubuntu mais potente

- **Tudo no servidor:** instale uv e Ollama lá e siga os mesmos passos. Sem cofre gráfico, a chave pode ficar numa variável de ambiente (`export GEMINI_API_KEY=...`).
- **Só o modelo no servidor:** rode o Ollama no Ubuntu e acesse por túnel SSH (`ssh -L 11435:127.0.0.1:11434 usuario@servidor`), usando o exemplo `servidor` em `config/settings.toml`. Não exponha a porta 11434 do Ollama na rede: ela não tem autenticação.

## Problemas comuns

| Mensagem | Causa provável |
| --- | --- |
| "Não consegui falar com o servidor local" | O Ollama não está aberto |
| "O modelo ... não está instalado no Ollama" | Falta `ollama pull <modelo>` |
| "Segredo 'GEMINI_API_KEY' não encontrado" | Falta o passo 5 |
| "O provedor não reconheceu o modelo" | O nome do modelo Gemini mudou: use o que aparece no AI Studio (ex.: `gemini-2.5-flash`) |
| "Limite de uso atingido" | Limites por minuto e por dia do plano gratuito |
| Resposta termina com "[resposta cortada...]" | Aumente `max_output_tokens` do modelo |

## Estrutura

```text
config/settings.toml        modelos, limites, porta; único arquivo que você precisa editar
config/system_prompt.md     instruções do assistente
src/secretario/
  app.py                    raiz de composição: escolhe as implementações
  config.py                 carga e validação da configuração (regras de segurança)
  secrets.py                cofre de chaves
  core/agent.py             o turno do agente e os comandos
  core/context.py           o que vai para o modelo a cada chamada
  core/session.py           conversa, modelo preferido, nível de sigilo
  core/types.py             tipos e eventos compartilhados
  llm/openai_compat.py      cliente para Ollama, Gemini e afins
  llm/router.py             escolha do modelo pelo sigilo
  llm/think_filter.py       remove blocos <think> do texto
  llm/fake.py               modelo falso para testes
  storage/db.py             SQLite e migrações
  audit/log.py              trilha de auditoria
  ui/                       Chainlit (adaptador fino), proteção de origem, inicialização
  cli.py                    conversa pelo terminal
tests/                      77 testes, incluindo cliente HTTP real contra servidor simulado
```

## Próximo passo: V1

Ferramentas e a camada de políticas: registro de ferramentas, gateway por onde passa toda chamada, aprovação humana pela interface, auditoria de cada execução e uma bateria de cenários de teste. O laço do agente em `core/agent.py` já está desenhado para receber essa etapa.

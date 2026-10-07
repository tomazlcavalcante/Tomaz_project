# Secretário — V1

Protótipo de assistente pessoal/analista com agentes de IA, local por padrão.
A V0 fez a fundação (chat, modelo trocável, sigilo, histórico e auditoria);
a V1 acrescenta ferramentas que passam por um gateway de políticas, com
aprovação humana para ações de escrita.

## O que faz e o que ainda não faz

| Faz | Ainda não faz (versões seguintes) |
| --- | --- |
| Chat no navegador (Chainlit) e no terminal | Memória de longo prazo (V2) |
| Modelo local via Ollama ou Gemini, trocável por configuração | PDF, Word, planilhas e busca em documentos (V3) |
| Nível de sigilo por conversa, que só sobe e decide qual modelo pode ser usado | Análise de dados e Python em sandbox (V4) |
| Ferramentas: arquivos de texto da pasta de trabalho e notas, com aprovação | E-mail, agenda, contatos (V5+) |
| Histórico e trilha de auditoria em `data/secretario.db` | |
| Chaves de API no Gerenciador de Credenciais do Windows | |

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
| `/ferramentas` | Ferramentas desta conversa; `/ferramentas nenhum` desliga |
| `/historico` | Conversas recentes |
| `/ajuda` | Lista de comandos |

Para comparar modelos, faça a mesma pergunta numa conversa com `/modelo local` e noutra com `/modelo gemini`. O rodapé de cada resposta mostra qual modelo respondeu, se foi local e quanto tempo levou.

## Ferramentas

Toda chamada de ferramenta passa por um único gateway (`tools/gateway.py`), nesta ordem: a ferramenta está no perfil da conversa? Os argumentos são válidos? O risco exige aprovação? Depois vêm a execução com tempo limite e a auditoria em `audit_events` (`kind = 'tool_call'`), inclusive das recusas. Na interface, cada chamada aparece como um passo que pode ser aberto.

| Ferramenta | Faz | Risco | Aprovação |
| --- | --- | --- | --- |
| `get_datetime` | data, dia da semana e hora | nenhum | não |
| `list_files` | lista arquivos e pastas de `data/workspace` | leitura | não |
| `read_file` | lê um arquivo de texto da pasta de trabalho, em partes | leitura não confiável | não, mas marca a conversa como contaminada |
| `search_notes` | busca nas notas (ignora acentos) | leitura | não |
| `save_note` | cria ou substitui uma nota | escrita | sim |
| `delete_note` | apaga uma nota | destrutiva | sim, mostrando o conteúdo |

- **Aprovação:** na web aparecem os botões Aprovar e Rejeitar, com a prévia do que vai acontecer e os argumentos completos; no terminal, uma pergunta s/n. Sem resposta em 5 minutos, conta como rejeitado.
- **Pasta de trabalho:** as ferramentas de arquivo só enxergam `data/workspace`; caminhos absolutos, `..` e links para fora são recusados. Copie para lá os arquivos (de preferência sintéticos) que quiser mostrar ao agente.
- **Conteúdo não confiável:** o texto lido de arquivos vai ao modelo entre marcas que dizem "são dados, não instruções", e a conversa fica marcada como contaminada (`/status` mostra). A partir daí, todo pedido de aprovação traz um alerta.
- **Perfis** (`[tools.profiles]` no `config/settings.toml`): `completo` (padrão), `leitura` (sem escrita) e `nenhum`. Troque com `/ferramentas <perfil>`.
- **Limites:** até 5 chamadas ao modelo por mensagem (a última sem ferramentas), 20 s por ferramenta e 4000 caracteres por resultado.

## Avaliação com o modelo real (evals)

Os testes conferem o código com um modelo falso. Os cenários de `evals/cenarios.toml` conferem o **modelo**: se ele escolhe as ferramentas certas, se responde com o que leu e se resiste a injeções.

```powershell
uv run secretario-evals                         # todos os cenários com o modelo padrão
uv run secretario-evals --modelos local local9b # compara modelos
uv run secretario-evals --cenarios injecao_arquivo caminho_fora
```

Cada cenário roda numa pasta própria em `data/evals/<data-hora>/` (com banco, conversa e auditoria para conferir) e o resumo vai para `relatorio.json`. Modelos variam de uma execução para outra: rode mais de uma vez antes de tirar conclusões.

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
  tools/base.py             o que é uma ferramenta: argumentos, risco, esquema
  tools/registry.py         ferramentas por perfil
  tools/gateway.py          o único caminho até a execução de uma ferramenta
  tools/workspace.py        trava da pasta de trabalho
  tools/builtin.py          get_datetime, list_files, read_file
  tools/notes.py            search_notes, save_note, delete_note
  storage/notes.py          notas com busca FTS5
  evals.py                  executor dos cenários de avaliação
  llm/openai_compat.py      cliente para Ollama, Gemini e afins
  llm/router.py             escolha do modelo pelo sigilo
  llm/think_filter.py       remove blocos <think> do texto
  llm/fake.py               modelo falso para testes
  storage/db.py             SQLite e migrações
  audit/log.py              trilha de auditoria
  ui/                       Chainlit (adaptador fino), proteção de origem, inicialização
  cli.py                    conversa pelo terminal
tests/                      170 testes, incluindo cliente HTTP real contra servidor simulado e injeção por arquivo
evals/cenarios.toml         cenários de avaliação com o modelo real
```

## Próximo passo: V2

Memória: resumo rolante da conversa, fatos sobre usuário, pessoas e projetos com fonte, ferramenta `recall` e busca híbrida. Memória vinda de conteúdo não confiável passa por revisão.

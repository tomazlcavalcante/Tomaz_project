# Secretário · V1

Protótipo de assistente pessoal/analista que roda na sua máquina.

**O que faz:** conversa com um modelo local (Ollama) ou, para dados públicos, com o Gemini; lista e lê arquivos de texto da pasta de trabalho (`data/workspace`); busca, cria e apaga notas. Criar ou apagar uma nota só acontece depois que você clica em **Aprovar**.

**O que ainda não faz:** memória de longo prazo, PDF e planilhas, e-mail, agenda ou internet.

**Comandos:** `/ajuda`, `/status`, `/modelos`, `/modelo <chave>`, `/sigilo <nível>`, `/ferramentas [perfil]`, `/historico`.

O nível de sigilo só sobe. Em "interno" ou acima, a conversa usa apenas o modelo local, mesmo que você tenha escolhido o Gemini. Depois que um arquivo é lido, a conversa fica marcada como contendo conteúdo não confiável: confira com atenção qualquer ação que o agente pedir para aprovar.

# Secretário · V0

Protótipo de assistente pessoal/analista que roda na sua máquina.

**O que a V0 faz:** conversa com um modelo de linguagem local (Ollama) ou, para dados públicos, com o Gemini; guarda as conversas e uma trilha de auditoria em `data/secretario.db`.

**O que ainda não faz:** usar ferramentas, ler arquivos, acessar e-mail, agenda ou internet. Isso começa na V1.

**Comandos:** `/ajuda`, `/status`, `/modelos`, `/modelo <chave>`, `/sigilo <nível>`, `/historico`.

O nível de sigilo só sobe. Em "interno" ou acima, a conversa usa apenas o modelo local, mesmo que você tenha escolhido o Gemini.

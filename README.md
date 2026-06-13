# Bot Telegram + LM Studio

Este é um bot para o Telegram que utiliza o seu servidor local do **LM Studio** como inteligência artificial, com **sistema multi-agente** e **memória de longo prazo**.

## Pré-requisitos

1. **Python 3.10+** instalado.
2. **LM Studio** rodando com um modelo carregado.

## Passo a Passo para Executar

### 1. Obtenha o Token do Telegram
1. Abra o Telegram e procure por `@BotFather`.
2. Mande o comando `/newbot`.
3. Siga as instruções para dar um nome e um username ao seu bot.
4. O BotFather lhe dará um **Token de Acesso** (ex: `123456789:ABCDEF...`). Guarde-o.

### 2. Configure o Projeto
1. Na pasta do projeto, crie um arquivo chamado `.env` baseado no arquivo `.env.example`:
   - Copie o arquivo `.env.example` e renomeie a cópia para `.env`
2. Abra o arquivo `.env` e cole o token do seu bot na variável `TELEGRAM_BOT_TOKEN`.
3. (Opcional) Configure as variáveis adicionais (veja `.env.example` para todas).

### 3. Inicie o LM Studio Local Server
1. Abra o LM Studio.
2. Carregue o modelo desejado (na aba superior).
   - *Nota: Se deseja enviar imagens, certifique-se de carregar um modelo de visão (Vision LLM).*
3. Vá para a aba **Local Server** (ícone <->).
4. Clique em **Start Server** (Certifique-se que o port é 1234, que é o padrão).

### 4. Instale as Dependências e Execute
```bash
# Crie um ambiente virtual (recomendado)
python -m venv venv
venv\Scripts\activate  # No Windows
# source venv/bin/activate  # No Linux/Mac

# Instale as dependências
pip install -r requirements.txt

# Execute o bot
python main.py
```

### 5. Converse com o Bot
Abra o Telegram, procure pelo username do seu bot e envie `/start`.

## 📸 Suporte a Imagens (Visão)
Envie uma foto no chat do Telegram com um modelo de visão carregado no LM Studio.

## Arquitetura do Projeto

```
telegram-lmstudio-bot/
├── main.py                         # Entry point (~130 linhas)
├── bot/
│   ├── config.py                   # Configuração centralizada
│   ├── handlers.py                 # Comandos do Telegram
│   ├── streaming.py                # Streaming LLM + roteamento
│   └── formatting.py               # Markdown → HTML
├── agents/
│   ├── __init__.py                 # Registry de agentes
│   ├── base.py                     # Classe base Agent
│   ├── router.py                   # Roteador inteligente (LLM)
│   ├── general.py                  # 💬 Conversas gerais
│   ├── coder.py                    # 💻 Programação
│   ├── researcher.py               # 🔍 Pesquisa e explicações
│   ├── creative.py                 # 🎨 Escrita criativa
│   └── analyst.py                  # 📊 Análise e matemática
├── persistence/
│   ├── history.py                  # Histórico de sessões
│   └── mempalace_adapter.py        # Memória de longo prazo
├── requirements.txt
└── .env
```

## Comandos Disponíveis

| Comando | Descrição |
|---|---|
| `/start` | Inicia a conversa com o bot |
| `/help` | Mostra a lista de comandos disponíveis |
| `/new` | Cria um novo chat limpo |
| `/chats` | Lista todos os chats salvos com prévia e nome |
| `/switch <id>` | Alterna para um chat específico da lista |
| `/rename <id> <nome>` | Renomeia um chat (ex: `/rename 1 Projeto X`) |
| `/clear` | Limpa a memória de conversação do chat atual |
| `/delete <id>` | Exclui um chat permanentemente da memória |
| `/retry` | Regenera a última resposta do assistente |
| `/export` | Exporta o chat atual como arquivo `.txt` |
| `/status` | Mostra diagnóstico do bot e do LM Studio |

### 🤖 Comandos Multi-Agente

| Comando | Descrição |
|---|---|
| `/agents` | Lista os agentes disponíveis com descrições |
| `/agent <nome>` | Força um agente para a próxima mensagem |
| `/agent auto` | Volta para roteamento automático |

### 🏛️ Comandos de Memória (MemPalace)

| Comando | Descrição |
|---|---|
| `/remember <query>` | Busca nas memórias de conversas passadas |
| `/memory` | Mostra status da memória de longo prazo |
| `/forget` | Apaga todas as memórias armazenadas |

## 🤖 Sistema Multi-Agente

O bot possui um **roteador inteligente** que analisa cada mensagem e decide automaticamente qual agente especializado deve responder:

| Agente | Emoji | Temperatura | Quando é usado |
|---|---|---|---|
| **General** | 💬 | 0.7 | Conversas gerais, saudações |
| **Coder** | 💻 | 0.3 | Programação, debugging, código |
| **Researcher** | 🔍 | 0.5 | Explicações, fatos, comparações |
| **Creative** | 🎨 | 1.0 | Escrita criativa, brainstorming |
| **Analyst** | 📊 | 0.4 | Matemática, dados, lógica |

### Exemplo
```
Você: "me ajuda a fazer uma API REST em Python"
Bot: 💻 Coder: Claro! Vou te ajudar a criar uma API REST...

Você: "escreve um poema sobre programação"
Bot: 🎨 Creative: No silêncio do código, bytes dançam...
```

### Configuração
```bash
AGENTS_ENABLED=true              # Ativar/desativar
ROUTER_MODEL=local-model         # Modelo do roteador
AGENT_CODER_ENABLED=true         # Habilitar agente de código
AGENT_RESEARCHER_ENABLED=true    # Habilitar agente de pesquisa
AGENT_CREATIVE_ENABLED=true      # Habilitar agente criativo
AGENT_ANALYST_ENABLED=true       # Habilitar agente analítico
```

## 🏛️ Memória de Longo Prazo (MemPalace)

O bot integra o **MemPalace** para memória semântica de longo prazo, permitindo que lembre de conversas anteriores.

### Como Funciona
1. **A cada mensagem**, busca memórias relevantes e injeta como contexto.
2. **Após cada resposta**, indexa automaticamente no MemPalace.
3. **Entre sessões**, mantém a memória — pergunte sobre conversas de dias atrás!

## 🔒 Segurança (Whitelist)
1. Coloque um ID aleatório em `ALLOWED_USER_IDS`.
2. Mande um Oi para o bot — ele responderá seu ID real.
3. Cole seu ID real no `.env` e reinicie o bot.

## 🛠️ Resolução de Problemas
- **Erro de Conexão**: O bot inicia mesmo sem LM Studio, mas mensagens falharão.
- **Respostas cortadas**: Divididas automaticamente em partes seguras.
- **MemPalace não inicializa**: Instale com `pip install mempalace`.
- **Roteador lento**: Configure `ROUTER_MODEL` para um modelo menor/mais rápido.

# Bot Telegram + LM Studio

Este é um bot para o Telegram que utiliza o seu servidor local do **LM Studio** como inteligência artificial.

## Pré-requisitos

1. **Python 3.8+** instalado.
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
3. (Opcional) Configure as variáveis adicionais:
   - `MODEL_NAME` — Nome do modelo carregado no LM Studio (padrão: `local-model`)
   - `TEMPERATURE` — Criatividade das respostas de 0.0 a 2.0 (padrão: `0.7`)
   - `SYSTEM_PROMPT` — Instrução de comportamento para o modelo
   - `MAX_HISTORY_LENGTH` — Limite de mensagens mantidas no histórico da sessão (padrão: `800`)

### 3. Inicie o LM Studio Local Server
1. Abra o LM Studio.
2. Carregue o modelo desejado (na aba superior).
   - *Nota: Se deseja enviar imagens, certifique-se de carregar um modelo de visão (Vision LLM).*
3. Vá para a aba **Local Server** (ícone <->).
4. Clique em **Start Server** (Certifique-se que o port é 1234, que é o padrão).

### 4. Instale as Dependências e Execute
Abra um terminal (ou prompt de comando) nesta pasta e execute:

```bash
# Opcional mas recomendado: Crie um ambiente virtual
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
O bot suporta o envio de imagens! Se você carregar um modelo compatível com visão no LM Studio (por exemplo, LLaVA ou similar):
1. Envie uma foto no chat do Telegram.
2. Adicione uma legenda se quiser fazer uma pergunta específica sobre ela.
3. O bot processará a imagem e responderá. (As imagens são enviadas em base64 e limpas do histórico salvo localmente para evitar arquivos gigantes).

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

### 🏛️ Comandos de Memória de Longo Prazo (MemPalace)

| Comando | Descrição |
|---|---|
| `/remember <query>` | Busca nas memórias de conversas passadas |
| `/memory` | Mostra status da memória de longo prazo |
| `/forget` | Apaga todas as memórias armazenadas |

## 🏛️ Memória de Longo Prazo (MemPalace)

O bot integra o **[MemPalace](https://github.com/MemPalace/mempalace)** para memória semântica de longo prazo. Isso permite que o bot **lembre de conversas anteriores** e injete contexto relevante automaticamente nas respostas.

### Como Funciona

1. **A cada mensagem**, o bot busca memórias relevantes no MemPalace e as injeta como contexto adicional para o modelo.
2. **Após cada resposta**, a conversa é indexada automaticamente no MemPalace para uso futuro.
3. **Entre sessões**, o bot mantém a memória — pergunte algo sobre uma conversa de dias atrás!

### Configuração

No arquivo `.env`:
```bash
MEMPALACE_ENABLED=true         # Ativar/desativar (padrão: true)
MEMPALACE_RESULTS=3            # Memórias a injetar por mensagem (padrão: 3)
MEMPALACE_WING=telegram_bot    # Nome do "wing" no palace
```

### Instalação

O MemPalace é instalado automaticamente com as dependências do projeto:
```bash
pip install -r requirements.txt
```

> **Nota:** Na primeira execução, o MemPalace baixará um modelo de embeddings (~500MB). As buscas subsequentes são locais e rápidas.

Para desabilitar o MemPalace, configure `MEMPALACE_ENABLED=false` no `.env`. O bot funcionará normalmente sem memória de longo prazo.

## 🔒 Segurança (Whitelist)
Por padrão o bot aceita qualquer usuário. Para limitar o acesso apenas a você:
1. Abra o arquivo `.env` e coloque uma numeração aleatória em `ALLOWED_USER_IDS` (ex: `123`).
2. Mande um Oi para o bot. Ele vai bloquear o acesso e responder o seu **ID real** no Telegram.
3. Copie o seu ID real, cole no `.env` (ex: `ALLOWED_USER_IDS=7119330385`) e reinicie o bot. Agora só você pode usá-lo.

## 🛠️ Resolução de Problemas (Troubleshooting)
- **Erro de Conexão com o LM Studio**: O bot inicia mesmo se o LM Studio estiver desligado, mas as mensagens falharão. Certifique-se de que o servidor local está rodando em `http://localhost:1234/v1` (ou a URL definida em `LM_STUDIO_URL`).
- **Respostas cortadas/malformadas**: Mensagens muito longas são divididas automaticamente em partes seguras, respeitando limites de palavra e formatação HTML. Se houver problemas, o bot faz fallback para texto cru.
- **Histórico corrompido**: O bot usa escrita atômica (write-tmp-then-rename) e garante salvamento no shutdown para evitar perda de dados.
- **MemPalace não inicializa**: Verifique se `mempalace` está instalado (`pip install mempalace`) e se `MEMPALACE_ENABLED=true` no `.env`. O bot funciona normalmente sem o MemPalace.

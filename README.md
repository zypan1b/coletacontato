# envioEVO

Scrap de números de empresas via **Google Places API (New)** + envio em massa via **Evolution API** com pausas aleatórias anti-ban e rotação de variantes de mensagem.

## Setup

```bash
# 1. Cria venv e instala deps
python -m venv .venv
source .venv/Scripts/activate          # Windows bash
# .venv\Scripts\activate                # Windows cmd/powershell
pip install -r requirements.txt

# 2. Configura tokens
cp .env.example .env
# edita .env com APIFY_TOKEN, EVO_URL, EVO_API_KEY, EVO_INSTANCE
```

## Uso

### Fluxo completo (scrape + send)

```bash
# 1. edita config.json -> queries (ex: "pizzaria em Sao Paulo")
python main.py scrape                  # gera data/results.json
python main.py send --dry-run          # simula envio
python main.py send                    # envia de verdade
```

### Envio direto de JSON (sem scrape)

```bash
python main.py send --from-json numbers.json.example --dry-run
python main.py send --from-json meu-arquivo.json
```

Formato esperado do JSON (ver `numbers.json.example`):

```json
[
  {"nome": "Pizzaria do Ze", "phone": "5511999990001"},
  {"nome": "Padaria Central", "phone": "+55 (11) 97777-0003"}
]
```

Números são normalizados (remove pontuação, prepende código do país se faltar).

## Configuração (`config.json`)

| Chave | Descrição |
|---|---|
| `scrape.queries` | Lista de buscas no Google Maps |
| `scrape.max_places_per_query` | Limite de lugares por query |
| `send.pause_min_seconds` / `pause_max_seconds` | Pausa aleatória entre envios |
| `send.max_messages_per_run` | 0 = sem limite |
| `send.skip_already_sent` | Pula números já no `data/sent.log` |
| `messages` | Lista de variantes; uma é sorteada por envio. Suporta `{nome}` |

## Anti-ban

- Pausa **random** entre `pause_min_seconds` e `pause_max_seconds` (default 30–90s)
- **Rotação aleatória** de variantes de mensagem
- **Checkpoint** em `data/sent.log` — relê `skip_already_sent: true` evita reenvio
- Sempre rode `--dry-run` primeiro pra validar template e formatação

## Arquivos

- `main.py` — CLI (`scrape` | `send`)
- `scraper.py` — cliente Apify + normalização de telefones
- `sender.py` — cliente Evolution API (`/message/sendText`)
- `config.json` — todas as configs editáveis
- `data/results.json` — output do scrape
- `data/sent.log` — log TSV de envios (timestamp, phone, status, variante)

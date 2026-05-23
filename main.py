"""envioEVO — scrap (Apify) + envio em massa via Evolution API com pausas anti-ban."""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from scraper import ApifyScraper, extract_leads, normalize_phone
from sender import EvolutionSender

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
RESULTS_FILE = DATA_DIR / "results.json"
SENT_LOG = DATA_DIR / "sent.log"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("envioEVO")


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_leads_from_json(path: Path, default_cc: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("JSON deve ser uma lista de objetos {nome, phone, ...}")
    leads, seen = [], set()
    for entry in raw:
        if isinstance(entry, str):
            entry = {"phone": entry, "nome": ""}
        phone = normalize_phone(entry.get("phone") or entry.get("telefone"), default_cc)
        if not phone or phone in seen:
            continue
        seen.add(phone)
        leads.append({
            "nome": entry.get("nome") or entry.get("name") or "",
            "phone": phone,
            **{k: v for k, v in entry.items() if k not in {"phone", "telefone", "nome", "name"}},
        })
    return leads


def load_blacklist() -> set[str]:
    if not BLACKLIST_FILE.exists():
        return set()
    try:
        data = json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except json.JSONDecodeError:
        return set()


def load_sent_set() -> set[str]:
    """Returns phones with a REAL successful send. Ignores dry-run and failures
    so the user can retry after a test or a connection issue."""
    if not SENT_LOG.exists():
        return set()
    sent = set()
    for line in SENT_LOG.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2] == "ok":
            sent.add(parts[1])
    return sent


def mark_sent(phone: str, status: str, message_idx: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(SENT_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{phone}\t{status}\tvariant_{message_idx}\n")


def cmd_scrape(args, cfg: dict) -> int:
    scfg = cfg["scrape"]
    terms = scfg.get("search_terms") or []
    locs = scfg.get("locations") or []
    if not terms or not locs:
        log.error("config.json precisa de 'scrape.search_terms' e 'scrape.locations' (listas)")
        return 2
    log.info("Apify (blueorion) — termos: %s | localizacoes: %s", terms, locs)
    scraper = ApifyScraper()
    items = scraper.run(
        search_terms=terms,
        locations=locs,
        max_items_per_location=scfg.get("max_items_per_location", 40),
        language=scfg.get("language", "pt-BR"),
        country_code=scfg.get("country_code", "BR"),
        mode=scfg.get("mode", "aggressive"),
        max_total_charge_usd=scfg.get("max_total_charge_usd"),
    )
    log.info("Apify retornou %d itens brutos", len(items))
    leads = extract_leads(items, default_country_code=cfg["send"]["country_code_default"])
    log.info("Extraidos %d leads unicos com telefone", len(leads))

    DATA_DIR.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Salvo em %s", RESULTS_FILE)
    return 0


def render_message(template: str, lead: dict) -> str:
    nome = (lead.get("nome") or "").strip() or "tudo bem"
    fields = {k: v for k, v in lead.items() if k != "nome"}
    try:
        return template.format(nome=nome, **fields)
    except (KeyError, IndexError):
        return template.replace("{nome}", nome)


def cmd_send(args, cfg: dict) -> int:
    scfg = cfg["send"]
    default_cc = scfg["country_code_default"]
    variants = cfg.get("messages") or []
    if not variants:
        log.error("config.json sem 'messages' — nada pra enviar.")
        return 2

    if args.from_json:
        leads = load_leads_from_json(Path(args.from_json), default_cc)
        log.info("%d leads carregados de %s", len(leads), args.from_json)
    else:
        if not RESULTS_FILE.exists():
            log.error("Sem %s. Rode `python main.py scrape` ou passe --from-json arquivo.json", RESULTS_FILE)
            return 2
        leads = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        log.info("%d leads carregados do scrape anterior", len(leads))

    if scfg.get("skip_already_sent", True):
        sent = load_sent_set()
        before = len(leads)
        leads = [l for l in leads if l["phone"] not in sent]
        log.info("Filtrando ja-enviados: %d -> %d", before, len(leads))

    blacklist = load_blacklist()
    if blacklist:
        before = len(leads)
        leads = [l for l in leads if l["phone"] not in blacklist]
        log.info("Filtrando blacklist: %d -> %d", before, len(leads))

    cap = scfg.get("max_messages_per_run", 0) or 0
    if cap > 0:
        leads = leads[:cap]
        log.info("Limitando a %d envios neste run", cap)

    if not leads:
        log.warning("Nada pra enviar.")
        return 0

    sender = None if args.dry_run else EvolutionSender()
    if sender:
        try:
            state = sender.check_connection()
            log.info("Evo connection state: %s", state.get("instance", state))
        except Exception as e:
            log.warning("Nao consegui validar conexao do Evo (seguindo mesmo assim): %s", e)

    pmin, pmax = scfg["pause_min_seconds"], scfg["pause_max_seconds"]
    ok = fail = 0

    for i, lead in enumerate(leads, 1):
        variant_idx = random.randint(0, len(variants) - 1)
        message = render_message(variants[variant_idx], lead)
        prefix = f"[{i}/{len(leads)}] {lead['phone']}"

        if args.dry_run:
            log.info("%s DRY-RUN variant=%d msg=%r", prefix, variant_idx, message[:80])
            mark_sent(lead["phone"], "dry-run", variant_idx)
            ok += 1
        else:
            try:
                resp = sender.send_text(lead["phone"], message)
                status = resp.get("status") or resp.get("key", {}).get("id", "sent")
                log.info("%s OK variant=%d status=%s", prefix, variant_idx, status)
                mark_sent(lead["phone"], "ok", variant_idx)
                ok += 1
            except Exception as e:
                log.error("%s FAIL: %s", prefix, e)
                mark_sent(lead["phone"], f"fail:{type(e).__name__}", variant_idx)
                fail += 1

        if i < len(leads):
            pause = random.uniform(pmin, pmax)
            log.info("  ...pausa %.1fs", pause)
            time.sleep(pause)

    log.info("Fim. ok=%d fail=%d", ok, fail)
    return 0 if fail == 0 else 1


def main() -> int:
    load_dotenv(BASE / ".env")

    parser = argparse.ArgumentParser(prog="envioEVO", description=__doc__)
    parser.add_argument("-c", "--config", default=str(BASE / "config.json"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scrape", help="Scrapeia via Apify e salva data/results.json")

    p_send = sub.add_parser("send", help="Envia mensagens via Evolution API com pausas")
    p_send.add_argument("--from-json", help="Caminho de JSON com numeros prontos (pula scrape)")
    p_send.add_argument("--dry-run", action="store_true", help="Simula sem chamar a Evo")

    args = parser.parse_args()
    cfg = load_config(Path(args.config))

    if args.cmd == "scrape":
        return cmd_scrape(args, cfg)
    if args.cmd == "send":
        return cmd_send(args, cfg)
    parser.error(f"Comando desconhecido: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

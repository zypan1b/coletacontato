"""envioEVO — dashboard Flask (visualizar leads, envios, acionar scrape/send)."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import (
    Flask, jsonify, redirect, render_template, request, send_file, url_for,
)

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
RESULTS_FILE = DATA_DIR / "results.json"
SENT_LOG = DATA_DIR / "sent.log"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"
JOBS_LOG = DATA_DIR / "jobs.log"

load_dotenv(BASE / ".env")

app = Flask(__name__)

_job_lock = threading.Lock()
_current_job: dict | None = None  # {pid, cmd, started_at, log_path}


# ---------- data loaders ----------

def load_leads() -> list[dict]:
    if not RESULTS_FILE.exists():
        return []
    try:
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def load_sent_entries() -> list[dict]:
    if not SENT_LOG.exists():
        return []
    entries = []
    for line in SENT_LOG.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        entries.append({
            "timestamp": parts[0],
            "phone": parts[1],
            "status": parts[2],
            "variant": parts[3],
        })
    return entries


def load_sent_phones() -> dict[str, dict]:
    """Phone -> latest entry."""
    out: dict[str, dict] = {}
    for e in load_sent_entries():
        out[e["phone"]] = e
    return out


def load_blacklist() -> set[str]:
    if not BLACKLIST_FILE.exists():
        return set()
    try:
        data = json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except json.JSONDecodeError:
        return set()


def save_blacklist(items: set[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    BLACKLIST_FILE.write_text(
        json.dumps(sorted(items), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def lead_status(phone: str, sent: dict[str, dict], blacklist: set[str]) -> str:
    if phone in blacklist:
        return "blacklist"
    if phone in sent:
        s = sent[phone]["status"]
        if s == "ok":
            return "sent"
        if s == "dry-run":
            return "dry-run"
        return "fail"
    return "pending"


def filter_leads(leads: list[dict], sent: dict, blacklist: set,
                 q: str = "", category: str = "", city: str = "", status: str = "") -> list[dict]:
    q_lower = q.strip().lower()
    cat_lower = category.strip().lower()
    city_lower = city.strip().lower()
    out = []
    for l in leads:
        if q_lower and q_lower not in (l.get("nome", "") + l.get("phone", "") + l.get("website", "")).lower():
            continue
        if cat_lower and cat_lower not in l.get("category", "").lower():
            continue
        if city_lower and city_lower not in l.get("address", "").lower():
            continue
        st = lead_status(l["phone"], sent, blacklist)
        if status and st != status:
            continue
        l = {**l, "_status": st}
        out.append(l)
    return out


# ---------- health checks ----------

def check_apify_balance() -> dict:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        return {"ok": False, "error": "APIFY_TOKEN ausente"}
    try:
        r = requests.get(
            "https://api.apify.com/v2/users/me/limits",
            params={"token": token}, timeout=8,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        limits = data.get("limits", {})
        current = data.get("current", {})
        cycle = data.get("monthlyUsageCycle", {})
        max_usd = limits.get("maxMonthlyUsageUsd") or 0
        used_usd = current.get("monthlyUsageUsd") or 0
        return {
            "ok": True,
            "limit_usd": max_usd,
            "used_usd": used_usd,
            "remaining_usd": max(0.0, max_usd - used_usd),
            "cycle_end": cycle.get("endAt", ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_evo_state() -> dict:
    url = (os.getenv("EVO_URL") or "").rstrip("/")
    key = os.getenv("EVO_API_KEY")
    inst = os.getenv("EVO_INSTANCE")
    if not (url and key and inst):
        return {"ok": False, "error": "EVO_URL/EVO_API_KEY/EVO_INSTANCE ausentes"}
    try:
        r = requests.get(
            f"{url}/instance/connectionState/{inst}",
            headers={"apikey": key}, timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        state = data.get("instance", {}).get("state") or "?"
        return {"ok": True, "state": state, "instance": inst}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- job runner ----------

def start_job(cmd_args: list[str]) -> tuple[bool, str]:
    """Spawn main.py subprocess. Returns (started, message)."""
    global _current_job
    with _job_lock:
        if _current_job and _is_running(_current_job["pid"]):
            return False, f"Job ja rodando (pid={_current_job['pid']}): {_current_job['cmd']}"
        DATA_DIR.mkdir(exist_ok=True)
        log_path = DATA_DIR / f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        f = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(BASE / "main.py"), *cmd_args],
            cwd=str(BASE), env=env, stdout=f, stderr=subprocess.STDOUT,
        )
        _current_job = {
            "pid": proc.pid,
            "cmd": " ".join(cmd_args),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "log_path": str(log_path),
        }
        with open(JOBS_LOG, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(_current_job, ensure_ascii=False) + "\n")
        return True, f"Job iniciado (pid={proc.pid})"


def _is_running(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import ctypes
            STILL_ACTIVE = 259
            PROCESS_QUERY = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY, False, pid)
            if not h:
                return False
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and exit_code.value == STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def current_job_status() -> dict | None:
    if not _current_job:
        return None
    running = _is_running(_current_job["pid"])
    out = {**_current_job, "running": running}
    log_path = Path(_current_job["log_path"])
    if log_path.exists():
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            out["log_tail"] = "\n".join(content.splitlines()[-30:])
        except Exception:
            out["log_tail"] = ""
    return out


# ---------- routes ----------

@app.route("/")
def index():
    leads = load_leads()
    sent = load_sent_phones()
    blacklist = load_blacklist()

    pending = sum(1 for l in leads if lead_status(l["phone"], sent, blacklist) == "pending")
    sent_ok = sum(1 for l in leads if lead_status(l["phone"], sent, blacklist) == "sent")
    failed = sum(1 for l in leads if lead_status(l["phone"], sent, blacklist) == "fail")
    blacklisted = sum(1 for l in leads if lead_status(l["phone"], sent, blacklist) == "blacklist")
    dryrun = sum(1 for l in leads if lead_status(l["phone"], sent, blacklist) == "dry-run")

    apify = check_apify_balance()
    evo = check_evo_state()
    job = current_job_status()

    config_summary = {}
    cfg_path = BASE / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            sc = cfg.get("scrape", {})
            sd = cfg.get("send", {})
            config_summary = {
                "search_terms": sc.get("search_terms", []),
                "locations": sc.get("locations", []),
                "max_items_per_location": sc.get("max_items_per_location"),
                "max_total_charge_usd": sc.get("max_total_charge_usd"),
                "pause_min": sd.get("pause_min_seconds"),
                "pause_max": sd.get("pause_max_seconds"),
                "variants": len(cfg.get("messages", [])),
            }
        except Exception:
            pass

    return render_template(
        "index.html",
        totals={
            "total": len(leads),
            "pending": pending,
            "sent": sent_ok,
            "failed": failed,
            "blacklist": blacklisted,
            "dry_run": dryrun,
        },
        apify=apify, evo=evo, job=job, config=config_summary,
    )


@app.route("/leads")
def leads_page():
    leads = load_leads()
    sent = load_sent_phones()
    blacklist = load_blacklist()
    q = request.args.get("q", "")
    cat = request.args.get("category", "")
    city = request.args.get("city", "")
    status = request.args.get("status", "")

    filtered = filter_leads(leads, sent, blacklist, q=q, category=cat, city=city, status=status)
    cats = sorted({l.get("category", "") for l in leads if l.get("category")})
    cities = sorted({_extract_city(l.get("address", "")) for l in leads if l.get("address")})
    return render_template(
        "leads.html",
        leads=filtered, total=len(leads),
        categories=cats, cities=[c for c in cities if c],
        filters={"q": q, "category": cat, "city": city, "status": status},
    )


def _extract_city(address: str) -> str:
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        candidate = parts[-2]
        return candidate.split("-")[0].strip()
    return ""


@app.route("/sent")
def sent_page():
    entries = list(reversed(load_sent_entries()))[:500]
    leads_idx = {l["phone"]: l for l in load_leads()}
    for e in entries:
        e["nome"] = leads_idx.get(e["phone"], {}).get("nome", "")
    return render_template("sent.html", entries=entries)


# ---------- blacklist actions ----------

@app.route("/blacklist/add", methods=["POST"])
def blacklist_add():
    phone = request.form.get("phone", "").strip()
    if phone:
        bl = load_blacklist()
        bl.add(phone)
        save_blacklist(bl)
    return redirect(request.referrer or url_for("leads_page"))


@app.route("/blacklist/remove", methods=["POST"])
def blacklist_remove():
    phone = request.form.get("phone", "").strip()
    if phone:
        bl = load_blacklist()
        bl.discard(phone)
        save_blacklist(bl)
    return redirect(request.referrer or url_for("leads_page"))


# ---------- exports ----------

@app.route("/export.csv")
def export_csv():
    leads = load_leads()
    sent = load_sent_phones()
    blacklist = load_blacklist()
    filtered = filter_leads(
        leads, sent, blacklist,
        q=request.args.get("q", ""),
        category=request.args.get("category", ""),
        city=request.args.get("city", ""),
        status=request.args.get("status", ""),
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["nome", "phone", "category", "address", "website", "status"])
    for l in filtered:
        w.writerow([l.get("nome", ""), l.get("phone", ""), l.get("category", ""),
                    l.get("address", ""), l.get("website", ""), l.get("_status", "")])
    out = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(
        out, mimetype="text/csv", as_attachment=True,
        download_name=f"envioevo-leads-{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )


@app.route("/export.json")
def export_json():
    leads = load_leads()
    sent = load_sent_phones()
    blacklist = load_blacklist()
    filtered = filter_leads(
        leads, sent, blacklist,
        q=request.args.get("q", ""),
        category=request.args.get("category", ""),
        city=request.args.get("city", ""),
        status=request.args.get("status", ""),
    )
    out = io.BytesIO(json.dumps(filtered, ensure_ascii=False, indent=2).encode("utf-8"))
    return send_file(
        out, mimetype="application/json", as_attachment=True,
        download_name=f"envioevo-leads-{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )


# ---------- job actions ----------

@app.route("/actions/scrape", methods=["POST"])
def action_scrape():
    cfg_file = request.form.get("config", "config.json")
    if request.form.get("confirm") != "yes":
        return ("Confirmacao ausente (?confirm=yes)", 400)
    ok, msg = start_job(["-c", cfg_file, "scrape"])
    return jsonify({"ok": ok, "message": msg})


@app.route("/actions/send", methods=["POST"])
def action_send():
    if request.form.get("confirm") != "yes":
        return ("Confirmacao ausente", 400)
    args = ["-c", request.form.get("config", "config.json"), "send"]
    if request.form.get("dry_run") == "1":
        args.append("--dry-run")
    if request.form.get("from_json"):
        args += ["--from-json", request.form["from_json"]]
    ok, msg = start_job(args)
    return jsonify({"ok": ok, "message": msg})


@app.route("/job/status")
def job_status_api():
    return jsonify(current_job_status() or {})


if __name__ == "__main__":
    port = int(os.getenv("DASH_PORT", "8080"))
    host = os.getenv("DASH_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)

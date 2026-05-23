"""Apify scraper client (blueorion/free-google-maps-scraper-simplified — pay-per-result, no min charge)."""
from __future__ import annotations

import os
import re
from typing import Iterable

import requests

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "blueorion~free-google-maps-scraper-simplified"


class ApifyScraper:
    def __init__(self, token: str | None = None, actor_id: str | None = None):
        self.token = token or os.getenv("APIFY_TOKEN")
        self.actor_id = actor_id or os.getenv("APIFY_ACTOR_ID", DEFAULT_ACTOR)
        if not self.token:
            raise RuntimeError("APIFY_TOKEN ausente no .env")

    def run(
        self,
        search_terms: Iterable[str],
        locations: Iterable[str],
        max_items_per_location: int = 40,
        language: str = "pt-BR",
        country_code: str = "BR",
        mode: str = "aggressive",
        max_total_charge_usd: float | None = None,
        timeout: int = 1800,
    ) -> list[dict]:
        url = f"{APIFY_BASE}/acts/{self.actor_id}/run-sync-get-dataset-items"
        payload: dict = {
            "searchTerms": list(search_terms),
            "startingLocations": list(locations),
            "maxItems": int(max_items_per_location),
            "language": language,
            "countryCode": country_code.upper(),
            "mode": mode,
        }
        params: dict = {"token": self.token}
        if max_total_charge_usd is not None:
            params["maxTotalChargeUsd"] = max_total_charge_usd
        r = requests.post(url, params=params, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()


def normalize_phone(raw: str, default_country_code: str = "55") -> str | None:
    """Strip non-digits; prepend country code if missing. Returns None if invalid."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    if len(digits) <= 11 and not digits.startswith(default_country_code):
        digits = default_country_code + digits
    if len(digits) < 10 or len(digits) > 15:
        return None
    return digits


def extract_leads(items: list[dict], default_country_code: str = "55") -> list[dict]:
    """Convert Apify Google Maps items into {nome, phone, address, category} leads."""
    leads = []
    seen_phones = set()
    for item in items:
        phone_raw = (
            item.get("phone")
            or item.get("phoneUnformatted")
            or item.get("phoneNumber")
            or item.get("internationalPhoneNumber")
        )
        phone = normalize_phone(phone_raw, default_country_code)
        if not phone or phone in seen_phones:
            continue
        seen_phones.add(phone)
        leads.append({
            "nome": item.get("title") or item.get("name") or "",
            "phone": phone,
            "address": item.get("address") or item.get("formattedAddress") or "",
            "category": item.get("categoryName") or item.get("category") or "",
            "website": item.get("website") or item.get("websiteUri") or "",
        })
    return leads

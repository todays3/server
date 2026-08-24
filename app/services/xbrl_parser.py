"""Parse DART XBRL/XML instance facts. Numbers never come from PDFs."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from app.services.dart_db import XbrlFact

log = logging.getLogger(__name__)

_SKIP_LOCAL = {
    "xbrl",
    "context",
    "unit",
    "schemaref",
    "identifier",
    "startdate",
    "enddate",
    "instant",
    "measure",
    "divide",
    "unitnumerator",
    "unitdenominator",
    "entity",
    "period",
    "scenario",
    "segment",
}


def parse_xbrl_xml(xml_text: str, *, ticker: str, year: int, quarter: int = 0) -> list[XbrlFact]:
    """Extract canonical numeric facts from an XBRL instance document."""
    code = (ticker or "").strip()
    if code.isdigit():
        code = code.zfill(6)
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        log.warning("xbrl parse failed for %s %s", code, year)
        return []

    by_account: dict[str, XbrlFact] = {}
    for elem in root.iter():
        local = _local_name(elem.tag)
        if local.lower() in _SKIP_LOCAL:
            continue
        mapped = canonicalize(local)
        if mapped is None:
            continue
        account, account_name = mapped
        value = _as_int(elem.text)
        if value is None:
            continue
        by_account[account] = XbrlFact(
            ticker=code,
            year=int(year),
            quarter=int(quarter),
            account=account,
            account_name=account_name,
            value=value,
        )
        if len(by_account) >= 20:
            break
    return list(by_account.values())


def canonicalize(local: str) -> tuple[str, str] | None:
    raw = local or ""
    name = raw.lower()
    if "textblock" in name or "abstract" in name or "comment" in name:
        return None
    if "costofsales" in name or "costofrevenue" in name:
        return None
    if "operating" in name and ("income" in name or "profit" in name):
        return "operating_profit", "영업이익"
    if "영업이익" in raw:
        return "operating_profit", "영업이익"
    if "profitloss" in name or "netincome" in name or "당기순이익" in raw:
        return "net_income", "당기순이익"
    if "순이익" in raw and "영업" not in raw:
        return "net_income", "당기순이익"
    if "revenue" in name or name in {"sales", "netsales"} or "매출" in raw:
        return "revenue", "매출액"
    return None


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.split(":")[-1]
    return tag


def _as_int(text: str | None) -> int | None:
    raw = (text or "").strip().replace(",", "").replace(" ", "")
    if not raw or raw.lower() in {"nil", "none", "-"}:
        return None
    try:
        return int(Decimal(raw))
    except (InvalidOperation, ValueError, OverflowError):
        return None

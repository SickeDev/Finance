"""Importação e exportação em XML.

Suporta dois modos de importação:

- replace: substitui a coleção inteira pelos dados do XML (preserva os ids).
- append : acrescenta ao que já existe, com deduplicação por "chave natural"
  (ex.: conta pelo nome+instituição, recorrente pelo nome). Referências entre
  coleções (compra->cartão, transação->conta) são resolvidas automaticamente,
  permitindo alimentar o sistema em vários uploads, inclusive gerados por IA.
"""

import datetime
import xml.etree.ElementTree as ET

from . import config
from . import storage as store

EXPORT_VERSION = "1.0"

# Ordem de processamento: coleções "pais" primeiro, referenciadas depois.
COLLECTION_ORDER = [
    "accounts",
    "cards",
    "boxes",
    "investments",
    "financings",
    "recurring",
    "card_purchases",
    "transactions",
    "networth",
]

# Campos que referenciam ids de outras coleções.
REF_FIELDS = {
    "card_purchases": ["card_id"],
    "transactions": ["account_id", "card_id", "financing_id", "recurring_id"],
    "recurring": ["account_id"],
    "boxes": ["account_id"],
    "investments": ["account_id"],
}


def _convert_value(text):
    if text is None:
        return ""
    s = text.strip()
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s == "":
        return ""
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def build_export_xml(storage):
    root = ET.Element(
        "finance-export",
        {
            "version": EXPORT_VERSION,
            "date": datetime.date.today().isoformat(),
            "currency": "BRL",
        },
    )
    for col in config.COLLECTIONS:
        parent = ET.SubElement(root, col)
        for doc in storage.list(col):
            item = ET.SubElement(parent, "item", {"id": doc.get("id", "")})
            for key in sorted(doc):
                if key == "id":
                    continue
                child = ET.SubElement(item, key)
                child.text = "" if doc[key] is None else str(doc[key])
    return ET.tostring(root, encoding="unicode")


def parse_import_xml(xml_bytes):
    root = ET.fromstring(xml_bytes)
    result = {}
    for child in root:
        col = child.tag
        if col not in config.COLLECTIONS:
            continue
        items = []
        for item in child.findall("item"):
            doc = {}
            doc_id = item.get("id") or None
            for field in item:
                doc[field.tag] = _convert_value(field.text)
            if doc_id:
                doc["id"] = doc_id
            items.append(doc)
        result[col] = items
    return result


def natural_key(collection, doc):
    """Identifica unicamente um documento para deduplicação."""
    name = str(doc.get("name", "") or "").strip().lower()
    ticker = str(doc.get("ticker", "") or doc.get("name", "") or "").strip().lower()
    if collection == "accounts":
        return ("name", name, str(doc.get("institution", "") or "").strip().lower())
    if collection == "cards":
        return ("name", name)
    if collection == "card_purchases":
        return (
            "purchase",
            doc.get("card_id", ""),
            str(doc.get("description", "") or "").strip().lower(),
            float(doc.get("amount", 0) or 0),
            str(doc.get("date", "") or ""),
        )
    if collection == "boxes":
        return ("name", name)
    if collection == "investments":
        return ("ticker", ticker)
    if collection == "financings":
        return ("name", name)
    if collection == "recurring":
        return ("name", name)
    if collection == "transactions":
        return (
            "tx",
            str(doc.get("date", "") or ""),
            str(doc.get("description", "") or "").strip().lower(),
            float(doc.get("amount", 0) or 0),
            str(doc.get("category", "") or "").strip().lower(),
        )
    if collection == "payments":
        return (
            "payment",
            str(doc.get("kind", "") or ""),
            str(doc.get("ref_id", "") or ""),
            str(doc.get("date", "") or ""),
            float(doc.get("amount", 0) or 0),
        )
    if collection == "networth":
        return ("date", str(doc.get("date", "") or ""))
    return ("id", str(doc.get("id", "")))


def append_import(storage, data):
    """Acrescenta os dados sem duplicar e resolve referências entre coleções.

    Retorna {coleção: {"added": n, "skipped": n, "updated": n}}.
    """
    refmap = {}
    counts = {}

    def register(doc, actual_id):
        nk = natural_key(doc["_col"], doc)
        refmap[("nk", nk)] = actual_id
        if doc["_col"] in ("accounts", "cards"):
            refmap[("name", str(doc.get("name", "") or "").strip().lower())] = actual_id
        src_id = doc.get("id")
        if src_id:
            refmap[("id", src_id)] = actual_id

    def resolve(col, doc):
        for field in REF_FIELDS.get(col, []):
            val = doc.get(field)
            if not val:
                continue
            if ("id", val) in refmap:
                doc[field] = refmap[("id", val)]
            elif ("name", str(val).strip().lower()) in refmap:
                doc[field] = refmap[("name", str(val).strip().lower())]

    def insert_docs(col, docs):
        existing = storage.list(col)
        keys = {}
        ids = set()
        for d in existing:
            keys[natural_key(col, d)] = d["id"]
            ids.add(d["id"])
        added = skipped = updated = 0
        for d in docs:
            d["_col"] = col
            resolve(col, d)
            nk = natural_key(col, d)
            src_id = d.get("id")
            if src_id and src_id in ids:
                d["id"] = src_id
                register(d, src_id)
                d.pop("_col", None)
                storage.update(col, src_id, d)
                updated += 1
            elif nk in keys:
                register(d, keys[nk])
                d.pop("_col", None)
                skipped += 1
            else:
                d["id"] = src_id or store.new_id()
                register(d, d["id"])
                d.pop("_col", None)
                storage.insert(col, d)
                keys[nk] = d["id"]
                ids.add(d["id"])
                added += 1
        counts[col] = {"added": added, "skipped": skipped, "updated": updated}

    # Coleções "pais" primeiro (para montar o mapa de referências).
    for col in ("accounts", "cards", "boxes", "investments", "financings", "recurring", "networth", "transfers", "settings"):
        if data.get(col):
            insert_docs(col, data[col])
    # Depois as que referenciam outras coleções.
    for col in ("card_purchases", "transactions", "payments"):
        if data.get(col):
            insert_docs(col, data[col])

    return counts

"""Leitura de extrato bancário com Google Gemini (API REST).

Recebe uma imagem (print/screenshot do extrato) e devolve uma lista estruturada
de transações detectadas: data, descrição, categoria, valor e tipo.

A chave da API vem de GEMINI_API_KEY (variável de ambiente) ou do arquivo
credentials/gemini_key.txt. O modelo padrão é gemini-2.0-flash (configurável
via GEMINI_MODEL).
"""

import base64
import datetime
import json
import os
import re

import requests

from . import config

PROMPT = """Analise esta imagem de um extrato bancário brasileiro.
Extraia TODAS as movimentações legíveis e retorne SOMENTE um JSON válido, sem
texto antes ou depois, neste formato:

{"items": [
  {"date": "YYYY-MM-DD", "description": "descrição curta", "amount": 123.45,
   "type": "expense", "category": "outros", "method": "pix"},
  ...
]}

Regras:
- date: use o formato ISO. Se o ano não aparecer, use o ano atual.
- amount: sempre positivo.
- type: "expense" para saídas/débitos, "income" para entradas/créditos.
- method: pix, cartão, débito, ted, boleto, dinheiro, salário, outro (omita se incerto).
- category: uma destas: moradia, alimentação, transporte, lazer, saúde, educação,
  cartão de crédito, investimentos, salário, freela, outros.
- NÃO inclua linhas de saldo (saldo anterior, saldo final, saldo em), nem totais,
  nem informações de conta/agência.
- Se a descrição indicar o tipo de compra (ex.: PIX enviado, TARIFA, COMPRA CRÉDITO),
  ajuste a categoria de forma razoável.
- Ignore valores irrelevantes como estornos que duplicam uma compra (mantenha só a
  movimentação principal).
- Se não houver movimentações legíveis, retorne {"items": []}.
"""


class AIError(Exception):
    """Falha ao consultar a IA."""


def _read_key():
    key = config.GEMINI_API_KEY
    if key:
        return key
    try:
        with open(config.GEMINI_KEY_FILE, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError:
        return ""
    return key


def is_configured():
    return bool(_read_key())


def save_key(key):
    """Persiste a chave da API no arquivo credentials/gemini_key.txt."""
    os.makedirs(os.path.dirname(config.GEMINI_KEY_FILE), exist_ok=True)
    with open(config.GEMINI_KEY_FILE, "w", encoding="utf-8") as fh:
        fh.write((key or "").strip())
    return key


def model_name():
    return config.GEMINI_MODEL


def _extract_json(text):
    """Extrai o primeiro objeto JSON válido do texto de resposta."""
    if not text:
        raise AIError("a IA não retornou conteúdo")
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AIError(f"resposta da IA não é um JSON válido: {exc}") from None


def _parse_amount(value):
    """Converte um valor em número, aceitando formatos brasileiros.

    Aceita "R$ 1.234,56", "1.234,56", "123,45", "123.45", "-50" e números.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _parse_date(value):
    """Normaliza a data para ISO (AAAA-MM-DD).

    Aceita AAAA-MM-DD, DD/MM/AAAA e DD/MM (ano assume o atual).
    """
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        day, month, year = text.split("/")
        return f"{year}-{month}-{day}"
    if re.fullmatch(r"\d{2}/\d{2}", text):
        day, month = text.split("/")
        return f"{datetime.date.today().year}-{month}-{day}"
    return ""


def _normalize_items(payload):
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise AIError("a IA não retornou uma lista de itens")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        date = _parse_date(it.get("date"))
        if not date:
            continue
        amount = _parse_amount(it.get("amount"))
        if amount is None or amount <= 0:
            continue
        ttype = "income" if str(it.get("type")).lower() in ("income", "entrada", "crédito", "credito") else "expense"
        out.append(
            {
                "date": date,
                "description": str(it.get("description") or "Transação").strip()[:200] or "Transação",
                "amount": round(amount, 2),
                "type": ttype,
                "category": str(it.get("category") or "outros").strip()[:60],
                "method": str(it.get("method") or "").strip()[:40],
            }
        )
    return out


def extract_statement(image_bytes, filename=""):
    """Envia a imagem ao Gemini e devolve a lista de transações normalizadas."""
    key = _read_key()
    if not key:
        raise AIError("chave da IA não configurada")

    mime = "image/jpeg"
    lower = filename.lower()
    if lower.endswith(".png"):
        mime = "image/png"
    elif lower.endswith(".webp"):
        mime = "image/webp"
    elif lower.endswith(".gif"):
        mime = "image/gif"
    elif lower.endswith(".bmp"):
        mime = "image/bmp"
    elif lower.endswith((".pdf",)):
        mime = "application/pdf"

    if mime == "application/pdf":
        data_b64 = base64.b64encode(image_bytes).decode()
        parts = [
            {"text": PROMPT},
            {
                "inline_data": {
                    "mime_type": "application/pdf",
                    "data": data_b64,
                }
            },
        ]
    else:
        data_b64 = base64.b64encode(image_bytes).decode()
        parts = [
            {"text": PROMPT},
            {"inline_data": {"mime_type": mime, "data": data_b64}},
        ]

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent?key={key}"
    )
    body = {"contents": [{"parts": parts}]}
    models = [config.GEMINI_MODEL] + list(config.GEMINI_MODEL_FALLBACKS)
    last_err = None
    for model in models:
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )
            res = requests.post(url, json=body, timeout=60)
        except requests.RequestException as exc:
            last_err = f"falha de rede ao consultar a IA: {exc}"
            continue

        if res.status_code == 200:
            break

        detail = res.text[:300]
        last_err = f"erro da API Gemini ({model}, HTTP {res.status_code}): {detail}"
        if res.status_code in (429, 404):
            continue
        raise AIError(last_err)
    else:
        if last_err and "HTTP 429" in last_err:
            raise AIError(
                "cota da API Gemini esgotada (HTTP 429). Tente de novo mais tarde "
                "ou gere uma nova chave gratuita em https://aistudio.google.com"
            )
        raise AIError(last_err)

    try:
        data = res.json()
    except ValueError as exc:
        raise AIError("resposta inválida da API Gemini") from None

    candidates = data.get("candidates") or []
    if not candidates:
        raise AIError("a IA não retornou candidatos (revise a imagem e tente de novo)")
    text = "".join(
        part.get("text", "")
        for part in (candidates[0].get("content") or {}).get("parts", [])
    )
    payload = _extract_json(text)
    return _normalize_items(payload)

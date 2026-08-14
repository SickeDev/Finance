"""Leitura de imagem (extrato e comprovante) com IA (OpenAI GPT ou Google Gemini).

Recebe uma imagem (print/screenshot do extrato ou comprovante) e devolve uma
lista estruturada de transações detectadas: data, descrição, categoria, valor
e tipo.

O provedor ativo é definido por AI_PROVIDER ("openai" ou "gemini"). A chave da
API vem da variável de ambiente correspondente (OPENAI_API_KEY / GEMINI_API_KEY)
ou dos arquivos credentials/openai_key.txt / credentials/gemini_key.txt.
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

RECEIPT_PROMPT = """Analise esta imagem de um COMPROVANTE de transação brasileiro
(ex.: comprovante de Pix, transferência, pagamento ou recebimento).
Extraia os dados principais e retorne SOMENTE um JSON válido, sem texto antes ou
depois, neste formato:

{"items": [
  {"date": "YYYY-MM-DD", "description": "descrição curta", "amount": 123.45,
   "type": "expense", "category": "outros", "method": "pix"},
  ...
]}

Regras:
- Retorne no máximo 1 item (a operação principal do comprovante). Se houver mais
  de uma operação no mesmo comprovante, retorne uma por operação.
- date: formato ISO. Se o ano não aparecer, use o ano atual.
- amount: sempre positivo.
- type: "expense" para pagamento/transferência/compra realizados, "income" para
  recebimento ou estorno a favor.
- method: pix, cartão, ted, boleto, dinheiro, outro (omita se incerto).
- description: resumo curto com o nome do pagador/recebedor quando legível
  (ex.: "PIX para João da Silva", "PIX de Maria").
- category: uma destas: moradia, alimentação, transporte, lazer, saúde, educação,
  cartão de crédito, investimentos, salário, freela, outros.
- NÃO inclua IDs de transação, códigos, saldo nem informações de conta/agência.
- Se não houver dados legíveis, retorne {"items": []}.
"""


class AIError(Exception):
    """Falha ao consultar a IA."""


def provider_name():
    return config.AI_PROVIDER


def _key_spec():
    """Devolve (chave_de_ambiente, caminho_do_arquivo) do provedor ativo."""
    if config.AI_PROVIDER == "openai":
        return config.OPENAI_API_KEY, config.OPENAI_KEY_FILE
    return config.GEMINI_API_KEY, config.GEMINI_KEY_FILE


def _read_key():
    env_key, file_path = _key_spec()
    if env_key:
        return env_key
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def is_configured():
    return bool(_read_key())


def save_key(key):
    """Persiste a chave do provedor ativo no arquivo credentials/."""
    _, file_path = _key_spec()
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write((key or "").strip())
    return key


def model_name():
    if config.AI_PROVIDER == "openai":
        return config.OPENAI_MODEL
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
    """Envia a imagem do extrato à IA ativa e devolve as transações normalizadas."""
    return _call_ai(image_bytes, filename, PROMPT)


def extract_receipt(image_bytes, filename=""):
    """Envia a imagem do comprovante à IA ativa e devolve a transação normalizada."""
    return _call_ai(image_bytes, filename, RECEIPT_PROMPT)


def _call_ai(image_bytes, filename="", prompt=PROMPT):
    """Chama o provedor ativo e normaliza a lista de itens."""
    if config.AI_PROVIDER == "openai":
        text = _call_openai(image_bytes, filename, prompt)
    else:
        text = _call_gemini(image_bytes, filename, prompt)
    payload = _extract_json(text)
    return _normalize_items(payload)


def _image_mime(filename):
    lower = (filename or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".bmp"):
        return "image/bmp"
    if lower.endswith(".pdf"):
        return "application/pdf"
    return "image/jpeg"


def _call_openai(image_bytes, filename="", prompt=PROMPT):
    """Envia a imagem ao OpenAI (chat completions, visão) e devolve o texto."""
    key = _read_key()
    if not key:
        raise AIError("chave da IA não configurada")

    mime = _image_mime(filename)
    if mime == "application/pdf":
        raise AIError("PDF não é aceito pelo OpenAI — envie um print/imagem do extrato ou comprovante")
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise AIError("formato de imagem não suportado pelo OpenAI (use PNG/JPG/WEBP/GIF)")

    data_b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": config.OPENAI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data_b64}"}},
                ],
            }
        ],
        "temperature": 0,
    }
    models = [config.OPENAI_MODEL] + list(config.OPENAI_MODEL_FALLBACKS)
    last_err = None
    for model in models:
        body["model"] = model
        try:
            res = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=60,
            )
        except requests.RequestException as exc:
            last_err = f"falha de rede ao consultar a IA: {exc}"
            continue

        if res.status_code == 200:
            break

        detail = res.text[:300]
        last_err = f"erro da API OpenAI ({model}, HTTP {res.status_code}): {detail}"
        if res.status_code in (429, 404):
            continue
        raise AIError(last_err)
    else:
        if last_err and "HTTP 429" in last_err:
            raise AIError(
                "sem créditos na conta da OpenAI (HTTP 429). Adicione créditos em "
                "https://platform.openai.com/settings/organization/billing ou use "
                "outra chave."
            )
        raise AIError(last_err)

    try:
        data = res.json()
    except ValueError as exc:
        raise AIError("resposta inválida da API OpenAI") from None

    choices = data.get("choices") or []
    if not choices:
        raise AIError("a IA não retornou conteúdo (revise a imagem e tente de novo)")
    return str((choices[0].get("message") or {}).get("content") or "")


def _call_gemini(image_bytes, filename="", prompt=PROMPT):
    """Envia a imagem ao Gemini com o prompt dado e devolve o texto da resposta."""
    key = _read_key()
    if not key:
        raise AIError("chave da IA não configurada")

    mime = _image_mime(filename)
    data_b64 = base64.b64encode(image_bytes).decode()
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": mime, "data": data_b64}},
    ]

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
    return "".join(
        part.get("text", "")
        for part in (candidates[0].get("content") or {}).get("parts", [])
    )

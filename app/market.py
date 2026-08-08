"""Integração com dados de mercado (Yahoo Finance, API gratuita sem chave).

Fornece:
- Cotações em tempo real de ações/FIIs/ETFs brasileiros (sufixo .SA) e cripto.
- Dividendo médio mensal por cota (últimos 12 meses) para estimativa de renda.
- Conversão de moeda (cripto em USD -> BRL via USDBRL=X).

A função `refresh_investment_prices` atualiza os investimentos no storage:
`current_price` (preço de mercado) e `dividend_monthly` (estimativa R$/cota/mês).
"""

import datetime
import time

import requests

from . import services

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) finance-app"}

# Cache simples por símbolo durante a execução de um refresh (preço + dividendos
# usam o mesmo chart). TTL curto para sempre ter dados frescos no uso manual.
_chart_cache = {}


def _chart_result(symbol):
    cached = _chart_cache.get(symbol)
    if cached and cached.get("t") > time.time():
        return cached["data"]
    data = _get(
        "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
        params={"range": "1y", "interval": "1mo", "events": "div"},
    )
    result = (data.get("chart") or {}).get("result") or []
    if not result or not result[0]:
        raise MarketError("símbolo não encontrado")
    _chart_cache[symbol] = {"data": result[0], "t": time.time() + 120}
    return result[0]

# Tipos de ativo que são tratados como cripto na hora de montar o símbolo Yahoo.
CRYPTO_TYPES = {"cripto", "crypto", "criptomoeda", "cryptocurrency"}

# Símbolos Yahoo para moedas digitais comuns.
CRYPTO_SYMBOLS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "USDT": "USDT-USD",
    "USDC": "USDC-USD",
    "BNB": "BNB-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
    "DOGE": "DOGE-USD",
    "LTC": "LTC-USD",
    "MATIC": "MATIC-USD",
    "AVAX": "AVAX-USD",
}


class MarketError(Exception):
    """Falha ao consultar o mercado (rede, símbolo inválido etc.)."""


def _get(url, params=None, timeout=12, retries=3):
    """GET com retry + backoff (o Yahoo costuma limitar requisições rápidas)."""
    for attempt in range(retries):
        try:
            res = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise MarketError(f"falha de rede: {exc}") from None
            time.sleep(0.6 * (attempt + 1))
            continue
        if res.status_code == 429 or res.status_code >= 500:
            if attempt == retries - 1:
                raise MarketError(f"HTTP {res.status_code} do Yahoo")
            time.sleep(0.6 * (attempt + 1))
            continue
        if res.status_code != 200:
            raise MarketError(f"HTTP {res.status_code} do Yahoo")
        try:
            return res.json()
        except ValueError as exc:
            raise MarketError("resposta inválida do Yahoo") from None
    raise MarketError("falha ao consultar o Yahoo")


def yahoo_symbol(ticker, atype=""):
    """Converte um ticker amigável em um símbolo do Yahoo Finance."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise MarketError("ticker vazio")
    if "." in ticker:
        return ticker
    if atype.strip().lower() in CRYPTO_TYPES:
        return CRYPTO_SYMBOLS.get(ticker, f"{ticker}-USD")
    if ticker in ("BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "USDT", "USDC", "BNB", "ADA", "AVAX", "MATIC"):
        return CRYPTO_SYMBOLS.get(ticker, f"{ticker}-USD")
    return f"{ticker}.SA"


def fetch_quote(symbol):
    """Retorna (preço, nome, moeda) da última cotação disponível."""
    result = _chart_result(symbol)
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        close = (result.get("indicators") or {}).get("close")
        quote = (result.get("indicators") or {}).get("quote") or []
        if quote and quote[0] and quote[0].get("close"):
            close = [c for c in quote[0]["close"] if c is not None]
            price = close[-1] if close else None
    if price is None:
        raise MarketError("preço não disponível")
    return {
        "price": float(price),
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "currency": meta.get("currency") or "BRL",
        "symbol": meta.get("symbol") or symbol,
        "time": meta.get("regularMarketTime"),
    }


def fetch_monthly_dividend(symbol):
    """Estimativa de dividendo médio por cota por mês (últimos 12 meses).

    Soma os proventos pagos nos últimos 12 meses e divide por 12. Retorna 0.0
    quando o ativo não pagou proventos no período (ou o símbolo é inválido).
    """
    try:
        result = _chart_result(symbol)
    except MarketError:
        return 0.0
    events = (result.get("events") or {}).get("dividends") or {}
    if not events:
        return 0.0
    total = 0.0
    for payload in events.values():
        try:
            total += float(payload.get("amount") or 0)
        except (TypeError, ValueError):
            continue
    return round(total / 12.0, 6)


def fetch_usd_brl():
    """Cotação USD/BRL atual (para converter cripto)."""
    try:
        quote = fetch_quote("USDBRL=X")
        return quote["price"]
    except MarketError:
        return None


def refresh_investment_prices(storage):
    """Atualiza preço atual e dividendo estimado de todos os investimentos.

    Retorna a lista de resultados por ativo:
    [{id, name, ticker, ok, price, currency, dividend_monthly, error}]
    Falhas de rede não interrompem os demais ativos.
    """
    results = []
    fx = None
    now = datetime.date.today().isoformat()

    for inv in sorted(storage.list("investments"), key=lambda i: i.get("name", "")):
        ticker = (inv.get("ticker") or "").strip()
        atype = inv.get("type", "")
        entry = {"id": inv["id"], "name": inv.get("name", ""), "ticker": ticker}
        if not ticker:
            entry.update({"ok": False, "error": "ticker não informado"})
            results.append(entry)
            continue

        try:
            symbol = yahoo_symbol(ticker, atype)
        except MarketError as exc:
            entry.update({"ok": False, "error": str(exc)})
            results.append(entry)
            continue

        try:
            quote = fetch_quote(symbol)
        except MarketError as exc:
            entry.update({"ok": False, "error": str(exc)})
            results.append(entry)
            continue

        price = quote["price"]
        if quote["currency"] != "BRL":
            if fx is None:
                fx = fetch_usd_brl()
            if fx is None:
                entry.update(
                    {
                        "ok": False,
                        "error": "não foi possível converter moeda (cotação USD/BRL indisponível)",
                    }
                )
                results.append(entry)
                continue
            price = price * fx

        dividend = fetch_monthly_dividend(symbol)

        inv["current_price"] = round(price, 6)
        inv["dividend_monthly"] = dividend
        inv["quote_date"] = now
        inv["quote_symbol"] = symbol
        storage.update("investments", inv["id"], inv)

        entry.update(
            {
                "ok": True,
                "price": round(price, 6),
                "currency": "BRL",
                "dividend_monthly": dividend,
                "quote_date": now,
                "quote_symbol": symbol,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        results.append(entry)

    return results


def investment_dividend_estimate(inv):
    """Estimativa mensal de dividendos do investimento (R$)."""
    qty = float(inv.get("quantity", 0))
    per_share = float(inv.get("dividend_monthly", 0))
    return round(qty * per_share, 2)


def total_dividend_estimate(storage):
    """Soma das estimativas mensais de dividendos de todos os ativos."""
    return round(
        sum(investment_dividend_estimate(i) for i in storage.list("investments")),
        2,
    )

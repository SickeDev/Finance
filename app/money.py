"""Validação e normalização de valores financeiros.

Converte entrada bruta (JSON, formulário) em valores limpos e seguros,
rejeitando números inválidos (NaN, infinito, negativo) e datas malformadas.
"""

import datetime
import math


def round2(value):
    return round(float(value), 2)


def money(value, minimum=0, maximum=None):
    """Valor monetário: número finito >= minimum, arredondado para 2 casas."""
    if isinstance(value, bool):
        raise ValueError("deve ser um número")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError("deve ser um número") from None
    if not math.isfinite(num):
        raise ValueError("deve ser um número válido")
    if num < minimum:
        raise ValueError(f"deve ser >= {minimum:g}")
    if maximum is not None and num > maximum:
        raise ValueError(f"deve ser <= {maximum:g}")
    return round(num, 2)


def quantity(value):
    """Quantidade (ex.: cotas): número finito >= 0, com precisão alta."""
    if isinstance(value, bool):
        raise ValueError("deve ser um número")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError("deve ser um número") from None
    if not math.isfinite(num) or num < 0:
        raise ValueError("deve ser um número maior ou igual a zero")
    return round(num, 8)


def integer(value, minimum=None, maximum=None):
    """Número inteiro, opcionalmente com limites."""
    if isinstance(value, bool):
        raise ValueError("deve ser um número inteiro")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("deve ser um número inteiro")
        num = int(value)
    except (TypeError, ValueError):
        raise ValueError("deve ser um número inteiro") from None
    if minimum is not None and num < minimum:
        raise ValueError(f"deve ser >= {minimum}")
    if maximum is not None and num > maximum:
        raise ValueError(f"deve ser <= {maximum}")
    return num


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "sim", "on"):
        return True
    if s in ("false", "0", "no", "nao", "off", ""):
        return False
    raise ValueError("deve ser verdadeiro/falso")


def iso_date(value):
    """Data no formato ISO 'YYYY-MM-DD' (aceita com hora)."""
    s = str(value).strip()
    try:
        datetime.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        raise ValueError(f"data inválida ('{s[:10] or 'vazia'}')") from None
    return s[:10]

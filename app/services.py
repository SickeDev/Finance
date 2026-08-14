"""Lógica de domínio do sistema financeiro.

Reúne cálculos (patrimônio, gastos, projeções), a trilha de pagamentos,
transferências entre entidades, integridade referencial e validação de dados.
A camada HTTP (app/routes.py) apenas chama estas funções e traduz erros.

Convenção: funções levantam `ValueError` com mensagem amigável para erros de
regra de negócio/validação, e `ReferenceBlocked` para exclusões bloqueadas.
"""

import datetime

from . import config
from . import money as M
from . import storage as store

# ---------------------------------------------------------------------------
# datas
# ---------------------------------------------------------------------------


def today():
    return datetime.date.today()


def add_months(d, n):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return datetime.date(y, m, 1)


def month_key(d):
    return d.strftime("%Y-%m")


def parse_date(value):
    """Data de um documento salvo; hoje se vazio ou inválido (dados legados)."""
    if not value:
        return today()
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return today()


# ---------------------------------------------------------------------------
# validação de entrada (whitelist por coleção)
# ---------------------------------------------------------------------------


class ReferenceBlocked(ValueError):
    """Exclusão recusada porque o registro ainda é referenciado."""


def _coerce(key, value, spec):
    kind = spec["type"]
    try:
        if kind == "str":
            s = "" if value is None else str(value).strip()
            mx = spec.get("max")
            if mx and len(s) > mx:
                raise ValueError(f"{key}: máximo de {mx} caracteres")
            return s
        if kind == "money":
            return M.money(value, minimum=spec.get("min", 0), maximum=spec.get("max"))
        if kind == "number":
            return M.quantity(value)
        if kind == "int":
            return M.integer(value, spec.get("min"), spec.get("max"))
        if kind == "bool":
            return M.to_bool(value)
        if kind == "date":
            return M.iso_date(value)
        if kind == "enum":
            v = str(value).strip().lower()
            if v not in spec["values"]:
                raise ValueError(f"{key}: valor inválido ('{v}')")
            return v
    except ValueError as exc:
        if str(exc).startswith(key):
            raise
        raise ValueError(f"{key}: {exc}") from None
    raise ValueError(f"{key}: tipo inválido")


def validate_doc(collection, body, partial=False):
    """Valida e normaliza um documento contra o schema da coleção.

    - Rejeita campos desconhecidos (whitelist).
    - Coage tipos e limites de cada campo.
    - Em criação (partial=False) exige os campos obrigatórios.
    """
    schema = config.SCHEMA.get(collection)
    if schema is None:
        return {k: v for k, v in body.items() if k != "id"}

    known = set(schema["fields"]) | {"id"}
    unknown = [str(k) for k in body if k not in known]
    if unknown:
        raise ValueError("campos desconhecidos: " + ", ".join(sorted(unknown)))

    out = {}
    for key, spec in schema["fields"].items():
        if key not in body:
            continue
        out[key] = _coerce(key, body[key], spec)

    if not partial:
        missing = []
        for req in schema["required"]:
            if req not in body:
                missing.append(req)
            elif isinstance(out.get(req), str) and not out[req].strip():
                missing.append(req)
        if missing:
            raise ValueError("campos obrigatórios: " + ", ".join(missing))
    return out


# ---------------------------------------------------------------------------
# cálculos de cartão / financiamento
# ---------------------------------------------------------------------------


def purchase_monthly(p):
    return M.round2(float(p.get("amount", 0)) / max(1, int(p.get("installments", 1))))


def purchase_next_due(p):
    d = parse_date(p.get("date"))
    count = int(p.get("paid_count", 0))
    return month_key(add_months(datetime.date(d.year, d.month, 1), count))


def card_remaining(storage):
    total = 0.0
    for p in storage.list("card_purchases"):
        if p.get("finished"):
            continue
        remaining = int(p.get("installments", 1)) - int(p.get("paid_count", 0))
        total += purchase_monthly(p) * remaining
    return M.round2(total)


def financing_remaining(f):
    return M.round2(
        float(f.get("monthly_value", 0))
        * (int(f.get("installments_total", 0)) - int(f.get("paid", 0)))
    )


def financing_active_in(fin, month):
    """True se o financiamento já começou antes/naquele mês."""
    start = fin.get("start_date")
    if not start:
        return True
    d = parse_date(start)
    return month_key(add_months(datetime.date(d.year, d.month, 1), 0)) <= month


def get_setting(storage, key, default=0):
    doc = storage.get("settings", key)
    if doc is None:
        return default
    try:
        return float(doc.get("value", default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# reajuste (ajuste manual de saldo/valor com histórico)
# ---------------------------------------------------------------------------
# Cada entidade expõe os campos que podem ser reajustados. A função
# apply_adjustment valida, aplica o novo valor e registra em "adjustments"
# para que a mudança fique documentada.

ADJUSTABLE = {
    "accounts": ("balance",),
    "boxes": ("balance",),
    "investments": ("quantity", "avg_price", "current_price"),
    "financings": ("monthly_value", "paid"),
    "recurring": ("amount",),
    "cards": ("limit",),
}


def _adjust_coerce(field, value):
    if field == "paid":
        return M.integer(value, 0)
    if field == "quantity":
        return M.quantity(value)
    return M.money(value)


def apply_adjustment(storage, body):
    """Ajusta o valor de um campo de uma entidade e registra o histórico."""
    coll = str(body.get("entity_type") or "").strip()
    entity_id = str(body.get("entity_id") or "").strip()
    field = str(body.get("field") or "").strip()
    if coll not in ADJUSTABLE:
        raise ValueError("tipo de entidade inválido")
    if field not in ADJUSTABLE[coll]:
        raise ValueError("campo não reajustável")
    if not entity_id:
        raise ValueError("entidade não informada")

    doc = storage.get(coll, entity_id)
    if doc is None:
        raise ValueError("não encontrado")

    old_value = float(doc.get(field) or 0)
    try:
        new_value = _adjust_coerce(field, body.get("new_value"))
    except ValueError as exc:
        raise ValueError(f"{field}: {exc}") from None

    doc[field] = new_value
    storage.update(coll, entity_id, doc)

    adjustment = {
        "id": store.new_id(),
        "date": body.get("date") or today().isoformat(),
        "entity_type": coll,
        "entity_id": entity_id,
        "entity_name": doc.get("name", ""),
        "field": field,
        "old_value": M.round2(old_value),
        "new_value": M.round2(new_value),
        "note": str(body.get("note") or "").strip()[:300],
    }
    storage.insert("adjustments", adjustment)

    # Reajuste de saldo (conta/caixinha) representa dinheiro de verdade:
    # registra como receita (aumento) ou despesa (redução) na transação.
    if field == "balance" and new_value != old_value:
        diff = new_value - old_value
        tx_account = adjustment["entity_id"] if coll == "accounts" else (doc.get("account_id") or "")
        storage.insert(
            "transactions",
            {
                "id": store.new_id(),
                "date": adjustment["date"],
                "description": f"Reajuste · {adjustment['entity_name']}",
                "category": "Outros",
                "amount": M.round2(abs(diff)),
                "type": "income" if diff > 0 else "expense",
                "account_id": tx_account,
                "method": "adjustment",
            },
        )
    return adjustment


# ---------------------------------------------------------------------------
# patrimônio / gastos / resumo
# ---------------------------------------------------------------------------


def compute_networth(storage):
    accounts = sum(float(a.get("balance", 0)) for a in storage.list("accounts"))
    # Caixinhas vinculadas a uma conta já estão dentro do saldo dela;
    # só caixinhas "soltas" (sem conta) contam como ativo separado.
    boxes = sum(
        float(b.get("balance", 0))
        for b in storage.list("boxes")
        if not b.get("account_id")
    )
    # O mesmo vale para investimentos vinculados: o valor deles já faz parte
    # do saldo da conta; só investimentos "soltos" contam como ativo separado.
    investments = sum(
        float(i.get("quantity", 0)) * float(i.get("current_price", 0))
        for i in storage.list("investments")
        if not i.get("account_id")
    )
    assets = M.round2(accounts + boxes + investments)

    cards = card_remaining(storage)
    fin = sum(financing_remaining(f) for f in storage.list("financings"))
    liabilities = M.round2(cards + fin)

    return {
        "accounts": M.round2(accounts),
        "boxes": M.round2(boxes),
        "investments": M.round2(investments),
        "assets": assets,
        "cards": cards,
        "financings": M.round2(fin),
        "liabilities": liabilities,
        "networth": M.round2(assets - liabilities),
    }


def month_spending(storage, month):
    income = 0.0
    expense = 0.0
    by_category = {}
    for t in storage.list("transactions"):
        if month_key(parse_date(t.get("date"))) != month:
            continue
        amount = float(t.get("amount", 0))
        ttype = t.get("type")
        if ttype == "income":
            income += amount
        elif ttype == "transfer":
            continue
        else:
            expense += amount
            cat = t.get("category") or "Outros"
            by_category[cat] = M.round2(by_category.get(cat, 0) + amount)
    return {"income": M.round2(income), "expense": M.round2(expense), "categories": by_category}


def bank_overview(storage):
    """Agrupa contas, caixinhas e investimentos banco por banco."""
    accounts = storage.list("accounts")
    boxes = storage.list("boxes")
    investments = storage.list("investments")

    def inv_value(i):
        return M.round2(float(i.get("quantity", 0)) * float(i.get("current_price", 0)))

    banks = []
    for a in accounts:
        acc_boxes = [b for b in boxes if b.get("account_id") == a["id"]]
        acc_inv = [i for i in investments if i.get("account_id") == a["id"]]
        boxes_total = sum(float(b.get("balance", 0)) for b in acc_boxes)
        inv_total = sum(inv_value(i) for i in acc_inv)
        banks.append(
            {
                "id": a["id"],
                "name": a.get("name", ""),
                "institution": a.get("institution", ""),
                "type": a.get("type", ""),
                "total": M.round2(float(a.get("balance", 0))),
                "free": M.round2(float(a.get("balance", 0)) - boxes_total - inv_total),
                "boxes": [
                    {
                        "id": b["id"],
                        "name": b.get("name", ""),
                        "balance": M.round2(float(b.get("balance", 0))),
                        "is_emergency": bool(b.get("is_emergency")),
                    }
                    for b in acc_boxes
                ],
                "investments": [
                    {
                        "id": i["id"],
                        "name": i.get("name", ""),
                        "ticker": i.get("ticker", ""),
                        "quantity": float(i.get("quantity", 0)),
                        "value": inv_value(i),
                    }
                    for i in acc_inv
                ],
            }
        )

    orphan_boxes = [
        {
            "id": b["id"],
            "name": b.get("name", ""),
            "balance": M.round2(float(b.get("balance", 0))),
        }
        for b in boxes
        if not b.get("account_id")
    ]
    orphan_investments = [
        {
            "id": i["id"],
            "name": i.get("name", ""),
            "ticker": i.get("ticker", ""),
            "quantity": float(i.get("quantity", 0)),
            "value": inv_value(i),
        }
        for i in investments
        if not i.get("account_id")
    ]
    return {"banks": banks, "orphan_boxes": orphan_boxes, "orphan_investments": orphan_investments}


def build_projection(storage, n_months=6):
    """Projeta saldo e gastos fixos dos próximos meses.

    Inclui contas recorrentes ativas, parcelas de financiamentos (respeitando
    a data de início) e a fatura do cartão de crédito do mês. A renda mensal
    vem das configurações (editável na tela).
    """
    start_balance = compute_networth(storage)["assets"]
    income = get_setting(storage, "monthly_income", 0.0)
    recurring = [r for r in storage.list("recurring") if r.get("active") is not False]
    financings = storage.list("financings")
    purchases = storage.list("card_purchases")

    fin_remaining = {
        f["id"]: int(f.get("installments_total", 0)) - int(f.get("paid", 0))
        for f in financings
    }

    months = []
    balance = start_balance
    for i in range(n_months):
        key = month_key(add_months(today(), i))

        rec_total = sum(float(r.get("amount", 0)) for r in recurring)

        fin_total = 0.0
        fin_items = []
        for f in financings:
            rem = fin_remaining.get(f["id"], 0)
            if rem <= 0:
                continue
            if not financing_active_in(f, key):
                continue
            monthly = float(f.get("monthly_value", 0))
            fin_total += monthly
            fin_items.append({"name": f.get("name", "Financiamento"), "amount": M.round2(monthly)})
            fin_remaining[f["id"]] = rem - 1

        inv_total = 0.0
        for p in purchases:
            if p.get("finished"):
                continue
            remaining = int(p.get("installments", 1)) - int(p.get("paid_count", 0))
            if remaining <= 0:
                continue
            due = parse_date(purchase_next_due(p) + "-01")
            last = add_months(due, remaining - 1)
            if month_key(due) <= key <= month_key(last):
                inv_total += purchase_monthly(p)

        total_out = M.round2(rec_total + fin_total + inv_total)
        balance = M.round2(balance + income - total_out)
        months.append(
            {
                "month": key,
                "recurring": M.round2(rec_total),
                "financings": M.round2(fin_total),
                "financing_items": fin_items,
                "invoice": M.round2(inv_total),
                "total": total_out,
                "balance": balance,
            }
        )

    return {"start": M.round2(start_balance), "income": income, "months": months}


def build_summary(storage):
    nw = compute_networth(storage)
    spending = month_spending(storage, month_key(today()))
    recent = sorted(
        storage.list("transactions"),
        key=lambda t: t.get("date", ""),
        reverse=True,
    )[:10]

    snapshots = sorted(storage.list("networth"), key=lambda s: s.get("date", ""))
    series = [
        {"date": s.get("date"), "total": M.round2(float(s.get("total", 0)))}
        for s in snapshots
        if s.get("total") is not None
    ]

    debts = []
    for f in storage.list("financings"):
        rem = financing_remaining(f)
        if rem > 0:
            debts.append({"name": f.get("name", "Financiamento"), "amount": rem})
    if nw["cards"] > 0:
        debts.append({"name": "Cartão de crédito", "amount": nw["cards"]})
    debts.sort(key=lambda d: d["amount"], reverse=True)

    return {
        "networth": nw,
        "spending": spending,
        "recent": recent,
        "series": series,
        "debts": debts,
        "month": month_key(today()),
        "banks": bank_overview(storage),
        "projection": build_projection(storage),
    }


def record_daily_snapshot(storage):
    """Registra o patrimônio do dia (id determinístico = data, sem duplicar)."""
    key = today().isoformat()
    if storage.get("networth", key) is not None:
        return
    if any(s.get("date") == key for s in storage.list("networth")):
        return
    storage.insert(
        "networth",
        {"id": key, "date": key, "total": compute_networth(storage)["networth"]},
    )


# ---------------------------------------------------------------------------
# entidades (transferências entre contas/caixinhas/investimentos)
# ---------------------------------------------------------------------------


def entity_value(etype, doc):
    if etype == "investment":
        return M.round2(float(doc.get("quantity", 0)) * float(doc.get("current_price", 0)))
    return M.round2(float(doc.get("balance", 0)))


def _units_from(amount, price):
    return round(amount / price, 8) if price else 0.0


def entity_debit(etype, doc, amount):
    if etype == "investment":
        price = float(doc.get("current_price", 0))
        if price <= 0:
            raise ValueError("preço do investimento deve ser maior que zero")
        qty = float(doc.get("quantity", 0))
        units = min(_units_from(amount, price), qty)
        doc["quantity"] = M.quantity(qty - units)
        return M.round2(units * price)
    doc["balance"] = M.round2(float(doc.get("balance", 0)) - amount)
    return amount


def entity_credit(etype, doc, amount):
    if etype == "investment":
        price = float(doc.get("current_price", 0))
        if price <= 0:
            raise ValueError("preço do investimento deve ser maior que zero")
        units = _units_from(amount, price)
        doc["quantity"] = M.quantity(float(doc.get("quantity", 0)) + units)
        return
    doc["balance"] = M.round2(float(doc.get("balance", 0)) + amount)


def create_transfer(storage, body):
    doc = validate_doc("transfers", body)
    if doc["amount"] <= 0:
        raise ValueError("valor deve ser maior que zero")
    if doc["from_type"] == doc["to_type"] and doc["from_id"] == doc["to_id"]:
        raise ValueError("origem e destino devem ser diferentes")

    from_coll = config.ENTITY_TYPES[doc["from_type"]]
    to_coll = config.ENTITY_TYPES[doc["to_type"]]
    src = storage.get(from_coll, doc["from_id"])
    dst = storage.get(to_coll, doc["to_id"])
    if src is None:
        raise ValueError("origem não encontrada")
    if dst is None:
        raise ValueError("destino não encontrado")

    amount = doc["amount"]
    current = entity_value(doc["from_type"], src)
    if current < amount - 0.005:
        raise ValueError(f"saldo insuficiente na origem ({current:.2f})")

    # Guarda a quantidade movida (investimentos) para reverter de forma exata,
    # sem recalcular pelo preço atual na hora do estorno.
    from_units = 0.0
    to_units = 0.0
    if doc["from_type"] == "investment":
        price = float(src.get("current_price", 0))
        if price <= 0:
            raise ValueError("preço do investimento de origem deve ser maior que zero")
        qty = float(src.get("quantity", 0))
        from_units = min(_units_from(amount, price), qty)
        src["quantity"] = M.quantity(qty - from_units)
        deducted = M.round2(from_units * price)
    else:
        src["balance"] = M.round2(float(src.get("balance", 0)) - amount)
        deducted = amount

    if doc["to_type"] == "investment":
        price = float(dst.get("current_price", 0))
        if price <= 0:
            raise ValueError("preço do investimento de destino deve ser maior que zero")
        to_units = _units_from(deducted, price)
        dst["quantity"] = M.quantity(float(dst.get("quantity", 0)) + to_units)
    else:
        dst["balance"] = M.round2(float(dst.get("balance", 0)) + deducted)

    storage.apply_updates([(from_coll, src), (to_coll, dst)])

    transfer = {
        "id": store.new_id(),
        "date": doc.get("date") or today().isoformat(),
        "description": doc.get("description") or "Transferência",
        "amount": M.round2(deducted),
        "from_type": doc["from_type"],
        "from_id": doc["from_id"],
        "to_type": doc["to_type"],
        "to_id": doc["to_id"],
        "from_units": round(from_units, 8),
        "to_units": round(to_units, 8),
    }
    storage.insert("transfers", transfer)

    from_name = src.get("name") or "Origem"
    to_name = dst.get("name") or "Destino"
    label = transfer["description"]
    if label == "Transferência":
        label = f"{from_name} → {to_name}"
    storage.insert(
        "transactions",
        {
            "id": store.new_id(),
            "date": transfer["date"],
            "description": f"Transferência · {label}",
            "category": "Outros",
            "amount": transfer["amount"],
            "type": "transfer",
            "account_id": "",
            "method": "transfer",
            "transfer_id": transfer["id"],
        },
    )
    return transfer


def delete_transfer(storage, transfer_id):
    t = storage.get("transfers", transfer_id)
    if t is None:
        raise ValueError("não encontrado")
    from_coll = config.ENTITY_TYPES.get(t.get("from_type"))
    to_coll = config.ENTITY_TYPES.get(t.get("to_type"))
    src = storage.get(from_coll, t.get("from_id")) if from_coll else None
    dst = storage.get(to_coll, t.get("to_id")) if to_coll else None

    updates = []
    if src is not None:
        if t.get("from_type") == "investment":
            src["quantity"] = M.quantity(
                float(src.get("quantity", 0)) + float(t.get("from_units") or 0)
            )
        else:
            src["balance"] = M.round2(float(src.get("balance", 0)) + float(t.get("amount", 0)))
        updates.append((from_coll, src))
    if dst is not None:
        if t.get("to_type") == "investment":
            dst["quantity"] = M.quantity(
                float(dst.get("quantity", 0)) - float(t.get("to_units") or 0)
            )
        else:
            dst["balance"] = M.round2(float(dst.get("balance", 0)) - float(t.get("amount", 0)))
        updates.append((to_coll, dst))
    if updates:
        storage.apply_updates(updates)
    tx_ids = [
        t["id"]
        for t in storage.list("transactions")
        if t.get("transfer_id") == transfer_id
    ]
    if tx_ids:
        storage.delete_many("transactions", tx_ids)
    storage.delete("transfers", transfer_id)


# ---------------------------------------------------------------------------
# transações manuais (origem/destino movendo saldo de verdade)
# ---------------------------------------------------------------------------
# Uma transação manual pode apontar para uma conta ou caixinha ("entity").
# Receita credita e despesa debita o saldo da entidade escolhida. Editar e
# excluir reverte o efeito anterior antes de aplicar o novo.


def _tx_movement(tx):
    """Retorna (coleção, id, delta) do efeito da transação, ou None."""
    etype = tx.get("entity_type") or ""
    eid = tx.get("entity_id") or ""
    coll = config.ENTITY_TYPES.get(etype)
    if coll not in ("accounts", "boxes") or not eid:
        return None
    amount = float(tx.get("amount") or 0)
    delta = amount if tx.get("type") == "income" else -amount
    return (coll, eid, delta)


def _check_sufficient(ent, delta):
    if delta >= 0:
        return
    current = float(ent.get("balance") or 0)
    if current < -delta - 0.005:
        name = ent.get("name") or "entidade"
        raise ValueError(f"saldo insuficiente em {name} ({current:.2f})")


def _apply_tx_movement(storage, tx):
    """Aplica (ou aplica de novo) o efeito de saldo da transação."""
    movement = _tx_movement(tx)
    if movement is None:
        return
    coll, eid, delta = movement
    ent = storage.get(coll, eid)
    if ent is None:
        return
    _check_sufficient(ent, delta)
    ent["balance"] = M.round2(float(ent.get("balance") or 0) + delta)
    storage.update(coll, eid, ent)


def _reverse_tx_movement(storage, tx):
    """Desfaz o efeito de saldo de uma transação já registrada."""
    movement = _tx_movement(tx)
    if movement is None:
        return
    coll, eid, delta = movement
    ent = storage.get(coll, eid)
    if ent is None:
        return
    ent["balance"] = M.round2(float(ent.get("balance") or 0) - delta)
    storage.update(coll, eid, ent)


def create_transaction(storage, body):
    """Cria uma transação manual, movendo o saldo da conta/caixinha escolhida."""
    doc = validate_doc("transactions", body)
    if doc.get("amount", 0) <= 0:
        raise ValueError("valor deve ser maior que zero")
    doc.setdefault("entity_type", "")
    doc.setdefault("entity_id", "")
    doc.setdefault("account_id", "")

    etype = doc.get("entity_type") or ""
    eid = doc.get("entity_id") or ""
    if eid:
        coll = config.ENTITY_TYPES.get(etype)
        if coll not in ("accounts", "boxes"):
            raise ValueError("entidade inválida: escolha uma conta ou caixinha")
        if storage.get(coll, eid) is None:
            raise ValueError("entidade não encontrada")
        if etype == "account":
            doc["account_id"] = eid
        if doc.get("type") != "transfer":
            _apply_tx_movement(storage, doc)

    doc["id"] = store.new_id()
    storage.insert("transactions", doc)
    return doc


def update_transaction(storage, tx_id, body):
    """Atualiza uma transação manual, revertendo o efeito antigo de saldo."""
    existing = storage.get("transactions", tx_id)
    if existing is None:
        raise ValueError("não encontrado")
    patch = validate_doc("transactions", body, partial=True)
    new = {**existing, **patch, "id": tx_id}

    old_movement = _tx_movement(existing)
    new_movement = _tx_movement(new)

    # Carrega cada entidade uma única vez (no Firestore cada get cria um dict
    # novo, então reutilizamos o mesmo objeto quando origem e destino batem).
    entities = {}

    def fetch(coll, eid):
        if (coll, eid) not in entities:
            entities[(coll, eid)] = storage.get(coll, eid)
        return entities[(coll, eid)]

    # Reverte o efeito antigo em memória antes de validar o novo.
    if old_movement:
        coll, eid, delta = old_movement
        ent = fetch(coll, eid)
        if ent is not None:
            ent["balance"] = M.round2(float(ent.get("balance") or 0) - delta)

    if new_movement:
        coll, eid, delta = new_movement
        ent = fetch(coll, eid)
        if ent is None:
            raise ValueError("entidade não encontrada")
        _check_sufficient(ent, delta)
        ent["balance"] = M.round2(float(ent.get("balance") or 0) + delta)

    if entities:
        storage.apply_updates([(c, e) for (c, _eid), e in entities.items()])

    if new.get("entity_type") == "account" and new.get("entity_id"):
        new["account_id"] = new["entity_id"]
    storage.update("transactions", tx_id, new)
    return new


# ---------------------------------------------------------------------------
# pagamentos (recorrentes, financiamentos, parcelas de cartão)
# ---------------------------------------------------------------------------
# Cada pagamento gera um registro em "payments" (trilha de auditoria). Se um
# conta for informada, também gera a transação de despesa correspondente.
# Estornar remove apenas o ÚLTIMO pagamento e a sua transação.


def _payments(storage, kind, ref_id):
    return [
        p for p in storage.list("payments")
        if p.get("kind") == kind and p.get("ref_id") == ref_id
    ]


def _last_payment(storage, kind, ref_id):
    rows = _payments(storage, kind, ref_id)
    if not rows:
        return None
    return sorted(rows, key=lambda p: (p.get("date", ""), p.get("id", "")))[-1]


def _paid_this_month(storage, kind, ref_id, when=None):
    month = month_key(parse_date(when))
    return any(month_key(parse_date(p.get("date"))) == month for p in _payments(storage, kind, ref_id))


def _record_payment(storage, kind, ref_id, date, amount, account_id, tx_id, description):
    storage.insert(
        "payments",
        {
            "id": store.new_id(),
            "kind": kind,
            "ref_id": ref_id,
            "date": date,
            "amount": M.round2(amount),
            "account_id": account_id or "",
            "tx_id": tx_id,
            "description": description or "",
        },
    )


def _make_expense(storage, account_id, payload):
    """Cria a transação de despesa (mesmo sem conta, para registro completo).

    Se a conta informada não existir, a transação é registrada sem conta,
    garantindo que todo pagamento sempre apareça na página Transações.
    """
    tx = {"id": store.new_id(), **payload, "date": payload["date"], "type": "expense"}
    if account_id and storage.get("accounts", account_id) is not None:
        tx["account_id"] = account_id
    else:
        tx["account_id"] = ""
    storage.insert("transactions", tx)
    return tx["id"]


def _purchase_tx(p):
    """Transação da compra no cartão: 1 lançamento com o valor TOTAL.

    A compra vira imediatamente uma despesa em Transações (no dia da compra).
    As parcelas continuam controlando a fatura mês a mês; pagar a fatura não
    cria despesa de novo (evita contar o gasto duas vezes).
    """
    return {
        "id": store.new_id(),
        "date": p.get("date") or today().isoformat(),
        "description": f"Cartão · {p.get('description', 'Compra')}",
        "category": p.get("category") or "Cartão de crédito",
        "amount": M.round2(float(p.get("amount", 0))),
        "type": "expense",
        "account_id": "",
        "method": "card",
        "card_id": p["id"],
    }


def _sync_purchase_tx(storage, p):
    """Mantém a transação da compra em dia ao editar a compra."""
    for t in storage.list("transactions"):
        if t.get("card_id") == p["id"] and t.get("method") == "card":
            t["date"] = p.get("date") or t["date"]
            t["description"] = f"Cartão · {p.get('description', 'Compra')}"
            t["category"] = p.get("category") or "Cartão de crédito"
            t["amount"] = M.round2(float(p.get("amount", 0)))
            storage.update("transactions", t["id"], t)
            return


def create_card_purchase(storage, body):
    """Cria a compra no cartão e já lança a transação (valor total)."""
    doc = validate_doc("card_purchases", body)
    if doc.get("amount", 0) <= 0:
        raise ValueError("valor deve ser maior que zero")
    doc["paid_count"] = 0
    doc["finished"] = False
    doc["id"] = store.new_id()
    storage.insert("card_purchases", doc)
    storage.insert("transactions", _purchase_tx(doc))
    return doc


def update_card_purchase(storage, purchase_id, body):
    """Atualiza a compra e sincroniza a transação vinculada."""
    existing = storage.get("card_purchases", purchase_id)
    if existing is None:
        raise ValueError("não encontrado")
    patch = validate_doc("card_purchases", body, partial=True)
    new = {**existing, **patch, "id": purchase_id}
    storage.update("card_purchases", purchase_id, new)
    _sync_purchase_tx(storage, new)
    return new


def pay_card_purchase(storage, purchase_id, account_id="", date=None):
    p = storage.get("card_purchases", purchase_id)
    if p is None:
        raise ValueError("não encontrado")
    if p.get("finished"):
        raise ValueError("esta compra já foi quitada")
    when = M.iso_date(date) if date else today().isoformat()
    monthly = purchase_monthly(p)
    if monthly <= 0:
        raise ValueError("valor da parcela inválido")
    if _paid_this_month(storage, "card", purchase_id, when):
        raise ValueError("esta parcela já foi paga neste mês")

    paid_count = int(p.get("paid_count", 0)) + 1
    p["paid_count"] = paid_count
    p["finished"] = paid_count >= int(p.get("installments", 1))

    # Pagar a parcela NÃO cria uma nova despesa: a compra já virou transação
    # (valor total) quando foi lançada. Aqui só marcamos a parcela como paga
    # e registramos na trilha de auditoria.
    storage.update("card_purchases", purchase_id, p)
    _record_payment(storage, "card", purchase_id, when, monthly, account_id, "",
                    p.get("description", "Compra"))
    return p


def unpay_card_purchase(storage, purchase_id):
    p = storage.get("card_purchases", purchase_id)
    if p is None:
        raise ValueError("não encontrado")
    payment = _last_payment(storage, "card", purchase_id)
    if payment is not None:
        if payment.get("tx_id"):
            storage.delete("transactions", payment["tx_id"])
        storage.delete("payments", payment["id"])
    p["paid_count"] = max(0, int(p.get("paid_count", 0)) - 1)
    p["finished"] = False
    storage.update("card_purchases", purchase_id, p)
    return p


def pay_financing(storage, fin_id, account_id="", date=None):
    f = storage.get("financings", fin_id)
    if f is None:
        raise ValueError("não encontrado")
    remaining = int(f.get("installments_total", 0)) - int(f.get("paid", 0))
    if remaining <= 0:
        raise ValueError("este financiamento já foi quitado")
    when = M.iso_date(date) if date else today().isoformat()
    monthly = float(f.get("monthly_value", 0))
    if monthly <= 0:
        raise ValueError("valor da parcela inválido")
    if _paid_this_month(storage, "financing", fin_id, when):
        raise ValueError("a parcela deste mês já foi paga")

    paid = int(f.get("paid", 0)) + 1
    f["paid"] = paid
    tx_id = _make_expense(
        storage,
        account_id,
        {
            "date": when,
            "description": f"Financiamento · {f.get('name', 'Financiamento')} ({paid}/{f.get('installments_total', 0)})",
            "amount": monthly,
            "category": f.get("category") or "Outros",
            "account_id": account_id,
            "method": "account",
            "financing_id": fin_id,
        },
    )
    storage.update("financings", fin_id, f)
    _record_payment(storage, "financing", fin_id, when, monthly, account_id, tx_id,
                    f.get("name", "Financiamento"))
    return f


def unpay_financing(storage, fin_id):
    f = storage.get("financings", fin_id)
    if f is None:
        raise ValueError("não encontrado")
    payment = _last_payment(storage, "financing", fin_id)
    if payment is not None:
        if payment.get("tx_id"):
            storage.delete("transactions", payment["tx_id"])
        storage.delete("payments", payment["id"])
    f["paid"] = max(0, int(f.get("paid", 0)) - 1)
    storage.update("financings", fin_id, f)
    return f


def pay_recurring(storage, rec_id, account_id="", date=None):
    r = storage.get("recurring", rec_id)
    if r is None:
        raise ValueError("não encontrado")
    if r.get("active") is False:
        raise ValueError("esta conta recorrente está inativa")
    when = M.iso_date(date) if date else today().isoformat()
    amount = float(r.get("amount", 0))
    if amount <= 0:
        raise ValueError("valor da conta inválido")
    if _paid_this_month(storage, "recurring", rec_id, when):
        raise ValueError("esta conta já foi paga neste mês")

    account_id = account_id or r.get("account_id") or ""
    tx_id = _make_expense(
        storage,
        account_id,
        {
            "date": when,
            "description": f"Recorrente · {r.get('name', 'Conta recorrente')}",
            "amount": amount,
            "category": r.get("category") or "Outros",
            "account_id": account_id,
            "method": "account",
            "recurring_id": rec_id,
        },
    )
    _record_payment(storage, "recurring", rec_id, when, amount, account_id, tx_id,
                    r.get("name", "Conta recorrente"))
    return {"ok": True, "paid": True, "date": when}


def unpay_recurring(storage, rec_id):
    if storage.get("recurring", rec_id) is None:
        raise ValueError("não encontrado")
    payment = _last_payment(storage, "recurring", rec_id)
    if payment is not None:
        if payment.get("tx_id"):
            storage.delete("transactions", payment["tx_id"])
        storage.delete("payments", payment["id"])
    return {"ok": True, "paid": False}


# ---------------------------------------------------------------------------
# integridade referencial (exclusão segura)
# ---------------------------------------------------------------------------

# Regras: coleção de origem -> lista de (coleção referenciadora, campo).
# transferências são verificadas à parte via from/to_type.
_REFERENCE_RULES = {
    "accounts": [
        ("boxes", "account_id"),
        ("investments", "account_id"),
        ("recurring", "account_id"),
        ("transactions", "account_id"),
    ],
    "cards": [
        ("card_purchases", "card_id"),
        ("transactions", "card_id"),
    ],
    "card_purchases": [
        ("payments", "ref_id"),
    ],
    "boxes": [],
    "investments": [],
    "financings": [
        ("payments", "ref_id"),
        ("transactions", "financing_id"),
    ],
    "recurring": [
        ("payments", "ref_id"),
        ("transactions", "recurring_id"),
    ],
    "transactions": [
        ("payments", "tx_id"),
    ],
    "transfers": [],
    "payments": [],
    "networth": [],
    "settings": [],
}

_ENTITY_KIND = {"accounts": "account", "boxes": "box", "investments": "investment"}


def _transfer_references(storage, collection, doc_id):
    kind = _ENTITY_KIND.get(collection)
    if not kind:
        return []
    ids = [
        t["id"]
        for t in storage.list("transfers")
        if (t.get("from_type") == kind and t.get("from_id") == doc_id)
        or (t.get("to_type") == kind and t.get("to_id") == doc_id)
    ]
    return ids


def references(storage, collection, doc_id):
    """Lista as referências existentes para um documento."""
    refs = []
    for coll, field in _REFERENCE_RULES.get(collection, []):
        ids = [d["id"] for d in storage.list(coll) if d.get(field) == doc_id]
        if ids:
            refs.append({"collection": coll, "count": len(ids)})
    t_ids = _transfer_references(storage, collection, doc_id)
    if t_ids:
        refs.append({"collection": "transfers", "count": len(t_ids)})
    return refs


def _delete_transfers_for(storage, kind, doc_id):
    ids = [
        t["id"]
        for t in storage.list("transfers")
        if (t.get("from_type") == kind and t.get("from_id") == doc_id)
        or (t.get("to_type") == kind and t.get("to_id") == doc_id)
    ]
    storage.delete_many("transfers", ids)


def _cascade_purchase(storage, purchase_id):
    payments = _payments(storage, "card", purchase_id)
    tx_ids = [p["tx_id"] for p in payments if p.get("tx_id")]
    if tx_ids:
        storage.delete_many("transactions", tx_ids)
    if payments:
        storage.delete_many("payments", [p["id"] for p in payments])
    card_tx = [
        t["id"]
        for t in storage.list("transactions")
        if t.get("card_id") == purchase_id and t.get("method") == "card"
    ]
    if card_tx:
        storage.delete_many("transactions", card_tx)


def _cascade_payments(storage, kind, ref_id):
    payments = _payments(storage, kind, ref_id)
    tx_ids = [p["tx_id"] for p in payments if p.get("tx_id")]
    if tx_ids:
        storage.delete_many("transactions", tx_ids)
    if payments:
        storage.delete_many("payments", [p["id"] for p in payments])


def _detach(storage, collection, doc_id):
    """Remove as dependências quando a exclusão é forçada."""
    if collection == "accounts":
        updates = []
        for coll in ("boxes", "investments", "recurring", "transactions"):
            for d in storage.list(coll):
                if d.get("account_id") == doc_id:
                    d["account_id"] = ""
                    updates.append((coll, d))
        if updates:
            storage.apply_updates(updates)
        _delete_transfers_for(storage, "account", doc_id)
    elif collection == "cards":
        purchase_ids = [
            p["id"]
            for p in storage.list("card_purchases")
            if p.get("card_id") == doc_id
        ]
        for pid in purchase_ids:
            _cascade_purchase(storage, pid)
        if purchase_ids:
            storage.delete_many("card_purchases", purchase_ids)
        updates = []
        for d in storage.list("transactions"):
            if d.get("card_id") == doc_id:
                d["card_id"] = ""
                updates.append(("transactions", d))
        if updates:
            storage.apply_updates(updates)
    elif collection == "card_purchases":
        _cascade_purchase(storage, doc_id)
    elif collection == "boxes":
        _delete_transfers_for(storage, "box", doc_id)
    elif collection == "investments":
        _delete_transfers_for(storage, "investment", doc_id)
    elif collection == "financings":
        _cascade_payments(storage, "financing", doc_id)
        updates = []
        for d in storage.list("transactions"):
            if d.get("financing_id") == doc_id:
                d["financing_id"] = ""
                updates.append(("transactions", d))
        if updates:
            storage.apply_updates(updates)
    elif collection == "recurring":
        _cascade_payments(storage, "recurring", doc_id)
        updates = []
        for d in storage.list("transactions"):
            if d.get("recurring_id") == doc_id:
                d["recurring_id"] = ""
                updates.append(("transactions", d))
        if updates:
            storage.apply_updates(updates)
    elif collection == "transactions":
        pay_ids = [
            p["id"]
            for p in storage.list("payments")
            if p.get("tx_id") == doc_id
        ]
        if pay_ids:
            storage.delete_many("payments", pay_ids)


def delete_doc(storage, collection, doc_id, force=False):
    """Exclui um documento, bloqueando se houver referências (a menos de force)."""
    doc = storage.get(collection, doc_id)
    if doc is None:
        raise ValueError("não encontrado")
    refs = references(storage, collection, doc_id)
    if refs and not force:
        detail = "; ".join(f"{r['collection']} ({r['count']})" for r in refs)
        raise ReferenceBlocked(
            "registro referenciado em " + detail + ". Exclua/desvincule antes "
            "ou repita com ?force=1 para remover as dependências."
        )
    if force:
        _detach(storage, collection, doc_id)
    if collection == "card_purchases":
        # A compra gera uma transação (valor total); ao excluir, remove junto.
        _cascade_purchase(storage, doc_id)
    if collection == "transactions":
        _reverse_tx_movement(storage, doc)
    storage.delete(collection, doc_id)
    return True

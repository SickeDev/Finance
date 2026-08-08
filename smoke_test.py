"""Smoke test: exercita todas as páginas e fluxos da API."""

import io
import os
import tempfile

from app import create_app
from app import storage as store

_DB = os.path.join(tempfile.gettempdir(), "smoke.db")
if os.path.exists(_DB):
    os.remove(_DB)
store.reset_storage_for_tests(path=_DB)
app = create_app()
app.config["TESTING"] = True
c = app.test_client()


def check(label, cond, extra=""):
    print(("OK  " if cond else "FAIL") + f" {label} {extra}")
    if not cond:
        raise SystemExit(1)


for u in ["/", "/contas", "/cartoes", "/caixinhas", "/investimentos",
          "/financiamentos", "/recorrentes", "/contas-a-pagar", "/transacoes", "/movimentacoes", "/dados"]:
    r = c.get(u)
    check(f"page {u}", r.status_code == 200, str(r.status_code))

acc = c.post("/api/accounts", json={"name": "Nubank", "balance": 500}).get_json()
card = c.post("/api/cards", json={"name": "Cartao", "limit": 1000, "due_day": 10}).get_json()
p = c.post("/api/card_purchases", json={
    "card_id": card["id"], "description": "Ten", "amount": 300,
    "installments": 3, "date": "2026-08-01"}).get_json()
check("purchase created", p.get("paid_count") == 0)

r = c.post(f"/api/card_purchases/{p['id']}/pay", json={"account_id": acc["id"]})
check("pay purchase", r.status_code == 200 and r.get_json()["paid_count"] == 1)

b = c.post("/api/boxes", json={"name": "Emerg", "balance": 100, "is_emergency": True}).get_json()
check("box with is_emergency", b.get("is_emergency") is True)

f = c.post("/api/financings", json={
    "name": "Fan", "monthly_value": 650, "installments_total": 20, "paid": 0}).get_json()
r = c.post(f"/api/financings/{f['id']}/pay", json={"account_id": acc["id"]})
check("pay financing", r.status_code == 200 and r.get_json()["paid"] == 1)

rec = c.post("/api/recurring", json={"name": "Netflix", "amount": 39.9, "account_id": acc["id"]}).get_json()
r = c.post(f"/api/recurring/{rec['id']}/pay", json={})
check("pay recurring", r.status_code == 200)

t = c.post("/api/transactions", json={
    "date": "2026-08-05", "description": "Salario", "amount": 5000,
    "type": "income", "category": "Salário", "account_id": acc["id"]}).get_json()
check("transaction type income", t.get("type") == "income")

inv = c.post("/api/investments", json={
    "name": "MXRF11", "quantity": 20, "current_price": 9.5}).get_json()
check("investment created", inv.get("quantity") == 20)

box2 = c.post("/api/boxes", json={"name": "Transferencia Fan", "balance": 0}).get_json()
expense_before = c.get("/api/summary").get_json()["spending"]["expense"]
r = c.post("/api/transfers", json={
    "from_type": "account", "from_id": acc["id"],
    "to_type": "box", "to_id": box2["id"],
    "amount": 200, "date": "2026-08-06", "description": "Guardar moto"})
check("transfer created", r.status_code == 201 and r.get_json()["amount"] == 200)
tf = r.get_json()
acc_after = [x for x in c.get("/api/accounts").get_json() if x["id"] == acc["id"]][0]
check("account debited", acc_after["balance"] == 300)
check("transfer not expense",
      c.get("/api/summary").get_json()["spending"]["expense"] == expense_before)

r = c.delete(f"/api/transfers/{tf['id']}")
check("transfer revert", r.status_code == 200)

s = c.get("/api/summary")
check("summary", s.status_code == 200)
nw = s.get_json()["networth"]
check("networth accounts", nw["accounts"] == 500)
check("networth boxes", nw["boxes"] == 100)
check("networth investments", nw["investments"] == 190)

exp = c.get("/api/export")
check("export", exp.status_code == 200 and len(exp.data) > 0)

res = c.post("/api/import", data={
    "file": (io.BytesIO(exp.data), "b.xml"), "mode": "append"},
    content_type="multipart/form-data")
check("reimport append (dedup)", res.status_code == 200)
counts = res.get_json()["counts"]
check("no duplicates on accounts", counts["accounts"]["added"] == 0)

meta = c.get("/api/meta").get_json()
check("meta backend local", meta["backend"] == "local")

print("\nSmoke test: todos os fluxos OK")

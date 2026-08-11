"""Testes end-to-end do sistema de finanças.

Executar:  python -m unittest test_app -v
"""

import io
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from app import create_app
from app import storage as store


class FinanceAppTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="finance_test_")
        store.reset_storage_for_tests(path=os.path.join(cls.tmp, "db.json"))
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for name in ("db.json", "db2.json"):
            p = os.path.join(self.tmp, name)
            if os.path.exists(p):
                os.remove(p)
        store.reset_storage_for_tests(path=os.path.join(self.tmp, "db.json"))

    def _post(self, url, data):
        res = self.client.post(url, json=data)
        self.assertEqual(res.status_code, 201, res.get_json())
        return res.get_json()

    def test_accounts_crud(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "institution": "Nubank", "type": "Conta corrente", "balance": 1200.5})
        self.assertTrue(acc["id"])

        acc["balance"] = 1300
        res = self.client.put(f"/api/accounts/{acc['id']}", json=acc)
        self.assertEqual(res.status_code, 200)

        rows = self.client.get("/api/accounts").get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["balance"], 1300)

        res = self.client.delete(f"/api/accounts/{acc['id']}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.client.get("/api/accounts").get_json()), 0)

    def test_adjustment_updates_and_records_history(self):
        acc = self._post("/api/accounts", {"name": "Conta teste", "balance": 100})
        res = self.client.post("/api/adjustments", json={
            "entity_type": "accounts",
            "entity_id": acc["id"],
            "field": "balance",
            "new_value": 150,
            "note": "rendimento",
        })
        self.assertEqual(res.status_code, 201)
        adj = res.get_json()
        self.assertEqual(adj["old_value"], 100)
        self.assertEqual(adj["new_value"], 150)

        rows = self.client.get("/api/accounts").get_json()
        self.assertEqual(rows[0]["balance"], 150)

        history = self.client.get("/api/adjustments").get_json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["entity_name"], "Conta teste")

    def test_adjustment_rejects_bad_field(self):
        acc = self._post("/api/accounts", {"name": "Conta teste", "balance": 100})
        res = self.client.post("/api/adjustments", json={
            "entity_type": "accounts",
            "entity_id": acc["id"],
            "field": "nao_existe",
            "new_value": 1,
        })
        self.assertEqual(res.status_code, 400)

        res = self.client.post("/api/adjustments", json={
            "entity_type": "inexistente",
            "entity_id": "x",
            "field": "balance",
            "new_value": 1,
        })
        self.assertEqual(res.status_code, 400)

    def test_card_installment_and_invoice(self):
        acc = self._post("/api/accounts", {"name": "Conta teste", "balance": 1000})
        card = self._post("/api/cards", {"name": "Cartão X", "brand": "Visa", "limit": 5000, "due_day": 10})
        compra = self._post("/api/card_purchases", {
            "card_id": card["id"], "date": "2026-08-01", "description": "Tenís",
            "category": "Lazer", "amount": 300, "installments": 3,
        })

        self.assertEqual(compra["paid_count"], 0)
        self.assertFalse(compra["finished"])

        res = self.client.post(f"/api/card_purchases/{compra['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(res.status_code, 200)
        paid = res.get_json()
        self.assertEqual(paid["paid_count"], 1)

        txs = self.client.get("/api/transactions").get_json()
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["amount"], 100.0)
        self.assertEqual(txs[0]["type"], "expense")

    def test_summary_networth(self):
        self._post("/api/accounts", {"name": "A", "balance": 1000})
        self._post("/api/boxes", {"name": "Emergência", "balance": 500, "is_emergency": True})
        self._post("/api/investments", {"name": "FII", "quantity": 10, "avg_price": 5, "current_price": 6})

        summary = self.client.get("/api/summary").get_json()
        nw = summary["networth"]
        self.assertEqual(nw["accounts"], 1000)
        self.assertEqual(nw["boxes"], 500)
        self.assertEqual(nw["investments"], 60)
        self.assertEqual(nw["assets"], 1560)
        self.assertEqual(nw["networth"], 1560)

        self._post("/api/transactions", {"date": "2026-08-01", "description": "Mercado", "amount": 80, "type": "expense", "category": "Alimentação"})
        summary = self.client.get("/api/summary").get_json()
        self.assertEqual(summary["spending"]["expense"], 80)
        self.assertIn("Alimentação", summary["spending"]["categories"])

    def test_xml_export_and_import(self):
        acc = self._post("/api/accounts", {"name": "XPTO", "balance": 777})

        export_res = self.client.get("/api/export")
        self.assertEqual(export_res.status_code, 200)
        xml_string = export_res.data.decode("utf-8")
        root = ET.fromstring(xml_string)
        self.assertEqual(root.tag, "finance-export")
        self.assertEqual(root.find("accounts").find("item").find("name").text, "XPTO")

        store.reset_storage_for_tests(path=os.path.join(self.tmp, "db2.json"))
        res = self.client.post("/api/import", data={
            "file": (io.BytesIO(xml_string.encode("utf-8")), "export.xml"),
            "mode": "append",
        }, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200, res.get_json())
        rows = self.client.get("/api/accounts").get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "XPTO")
        self.assertEqual(rows[0]["balance"], 777)

    def test_xlsx_import(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Contas"
        ws.append(["Nome", "Instituição", "Saldo"])
        ws.append(["Nubank", "Nubank", 100.5])
        ws.append(["Mercado Pago", "MP", "1.200,50"])

        ws2 = wb.create_sheet("Transações")
        ws2.append(["Data", "Descrição", "Valor", "Tipo", "Categoria", "Conta"])
        ws2.append(["01/08/2026", "Mercado", 50, "expense", "Alimentação", "Nubank"])
        ws2.append(["2026-08-02", "Salário", 5000, "income", "Salário", "Mercado Pago"])

        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()

        store.reset_storage_for_tests(path=os.path.join(self.tmp, "db2.json"))
        res = self.client.post("/api/import", data={
            "file": (io.BytesIO(xlsx_bytes), "dados.xlsx"),
            "mode": "append",
        }, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200, res.get_json())

        accounts = self.client.get("/api/accounts").get_json()
        self.assertEqual(len(accounts), 2)
        nubank = next(a for a in accounts if a["name"] == "Nubank")
        mp = next(a for a in accounts if a["name"] == "Mercado Pago")
        self.assertEqual(nubank["balance"], 100.5)
        self.assertEqual(mp["balance"], 1200.5)

        txs = self.client.get("/api/transactions").get_json()
        self.assertEqual(len(txs), 2)
        merc = next(t for t in txs if t["description"] == "Mercado")
        self.assertEqual(merc["date"], "2026-08-01")
        self.assertEqual(merc["account_id"], nubank["id"])

        res = self.client.post("/api/import", data={
            "file": (io.BytesIO(xlsx_bytes), "dados.xlsx"),
            "mode": "append",
        }, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(len(self.client.get("/api/accounts").get_json()), 2)
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 2)

    def test_recurring_pay(self):
        acc = self._post("/api/accounts", {"name": "C", "balance": 500})
        rec = self._post("/api/recurring", {"name": "Netflix", "amount": 55.9, "category": "Lazer", "account_id": acc["id"]})
        res = self.client.post(f"/api/recurring/{rec['id']}/pay", json={})
        self.assertEqual(res.status_code, 200)
        txs = self.client.get("/api/transactions").get_json()
        self.assertTrue(any(t["description"] == "Recorrente · Netflix" and t["amount"] == 55.9 for t in txs))

    def test_recurring_pay_without_account_still_creates_transaction(self):
        rec = self._post("/api/recurring", {"name": "Apartamento", "amount": 510, "category": "Moradia"})
        res = self.client.post(f"/api/recurring/{rec['id']}/pay", json={})
        self.assertEqual(res.status_code, 200)
        txs = self.client.get("/api/transactions").get_json()
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["description"], "Recorrente · Apartamento")
        self.assertEqual(txs[0]["account_id"], "")

    def test_transfer_creates_and_reverts_transaction(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        box = self._post("/api/boxes", {"name": "Reserva", "balance": 0})
        res = self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "box", "to_id": box["id"],
            "amount": 300, "date": "2026-08-05",
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        t = res.get_json()

        txs = self.client.get("/api/transactions").get_json()
        self.assertEqual(len(txs), 1)
        tx = txs[0]
        self.assertEqual(tx["type"], "transfer")
        self.assertEqual(tx["amount"], 300)
        self.assertEqual(tx["description"], "Transferência · Nubank → Reserva")
        self.assertEqual(tx["transfer_id"], t["id"])

        res = self.client.delete(f"/api/transfers/{t['id']}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def test_balance_adjustment_creates_income_transaction(self):
        acc = self._post("/api/accounts", {"name": "Conta teste", "balance": 100})
        res = self.client.post("/api/adjustments", json={
            "entity_type": "accounts",
            "entity_id": acc["id"],
            "field": "balance",
            "new_value": 150,
            "note": "rendimento",
        })
        self.assertEqual(res.status_code, 201)
        txs = self.client.get("/api/transactions").get_json()
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["type"], "income")
        self.assertEqual(txs[0]["amount"], 50)
        self.assertEqual(txs[0]["description"], "Reajuste · Conta teste")

    def test_non_balance_adjustment_does_not_create_transaction(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 100})
        self._post("/api/investments", {"name": "FII", "quantity": 10, "current_price": 10})
        inv = self.client.get("/api/investments").get_json()[0]
        res = self.client.post("/api/adjustments", json={
            "entity_type": "investments",
            "entity_id": inv["id"],
            "field": "current_price",
            "new_value": 12,
        })
        self.assertEqual(res.status_code, 201)
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def _get(self, collection, doc_id):
        rows = self.client.get(f"/api/{collection}").get_json()
        return next(r for r in rows if r["id"] == doc_id)

    def test_transaction_expense_debits_entity_account(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        res = self.client.post("/api/transactions", json={
            "date": "2026-08-10", "description": "Mercado", "amount": 150,
            "type": "expense", "category": "Alimentação",
            "entity_type": "account", "entity_id": acc["id"],
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        tx = res.get_json()
        self.assertEqual(tx["entity_type"], "account")
        self.assertEqual(tx["account_id"], acc["id"])
        self.assertEqual(self._get("accounts", acc["id"])["balance"], 850)

    def test_transaction_income_credits_box(self):
        box = self._post("/api/boxes", {"name": "Reserva", "balance": 0})
        res = self.client.post("/api/transactions", json={
            "date": "2026-08-10", "description": "Bônus", "amount": 500,
            "type": "income", "category": "Salário",
            "entity_type": "box", "entity_id": box["id"],
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        tx = res.get_json()
        self.assertEqual(tx["entity_type"], "box")
        self.assertEqual(self._get("boxes", box["id"])["balance"], 500)

    def test_transaction_expense_insufficient_funds_blocked(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 100})
        res = self.client.post("/api/transactions", json={
            "date": "2026-08-10", "description": "Carro", "amount": 300,
            "type": "expense", "category": "Outros",
            "entity_type": "account", "entity_id": acc["id"],
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("saldo insuficiente", res.get_json()["error"])
        self.assertEqual(self._get("accounts", acc["id"])["balance"], 100)

    def test_transaction_edit_updates_balances(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        box = self._post("/api/boxes", {"name": "Reserva", "balance": 300})
        tx = self._post("/api/transactions", {
            "date": "2026-08-10", "description": "Compras", "amount": 100,
            "type": "expense", "category": "Outros",
            "entity_type": "account", "entity_id": acc["id"],
        })
        self.assertEqual(self._get("accounts", acc["id"])["balance"], 900)

        # muda para caixinha e outro valor: reverte a conta e debita a caixinha
        res = self.client.put(f"/api/transactions/{tx['id']}", json={
            "amount": 250, "entity_type": "box", "entity_id": box["id"],
        })
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self._get("accounts", acc["id"])["balance"], 1000)
        self.assertEqual(self._get("boxes", box["id"])["balance"], 50)

    def test_transaction_delete_reverses_balance(self):
        box = self._post("/api/boxes", {"name": "Reserva", "balance": 100})
        tx = self._post("/api/transactions", {
            "date": "2026-08-10", "description": "Presente", "amount": 40,
            "type": "expense", "category": "Lazer",
            "entity_type": "box", "entity_id": box["id"],
        })
        self.assertEqual(self._get("boxes", box["id"])["balance"], 60)
        res = self.client.delete(f"/api/transactions/{tx['id']}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._get("boxes", box["id"])["balance"], 100)
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def test_transaction_entity_rejects_unknown_type(self):
        res = self.client.post("/api/transactions", json={
            "date": "2026-08-10", "description": "X", "amount": 10,
            "type": "expense", "entity_type": "investment", "entity_id": "abc",
        })
        self.assertEqual(res.status_code, 400)

    def test_transfers_update_balances_and_revert(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        box = self._post("/api/boxes", {"name": "Reserva", "balance": 200})
        inv = self._post("/api/investments", {"name": "FII", "quantity": 10, "current_price": 10})

        res = self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "box", "to_id": box["id"],
            "amount": 300, "date": "2026-08-05", "description": "Guardar",
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        t = res.get_json()
        self.assertEqual(t["amount"], 300)

        a = self._get("accounts", acc["id"])
        b = self._get("boxes", box["id"])
        self.assertEqual(a["balance"], 700)
        self.assertEqual(b["balance"], 500)

        res = self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "investment", "to_id": inv["id"],
            "amount": 150, "date": "2026-08-06",
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        inv_after = self._get("investments", inv["id"])
        self.assertEqual(inv_after["quantity"], 25)

        res = self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "box", "to_id": box["id"],
            "amount": 99999,
        })
        self.assertEqual(res.status_code, 400)

        self.assertEqual(len(self.client.get("/api/transfers").get_json()), 2)

        res = self.client.delete(f"/api/transfers/{t['id']}")
        self.assertEqual(res.status_code, 200)
        a = self._get("accounts", acc["id"])
        b = self._get("boxes", box["id"])
        self.assertEqual(a["balance"], 850)
        self.assertEqual(b["balance"], 200)
        self.assertEqual(len(self.client.get("/api/transfers").get_json()), 1)

    def test_transfers_do_not_count_as_expense(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        box = self._post("/api/boxes", {"name": "Reserva", "balance": 0})
        self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "box", "to_id": box["id"],
            "amount": 400, "date": "2026-08-05",
        })

        summary = self.client.get("/api/summary").get_json()
        self.assertEqual(summary["spending"]["expense"], 0)
        self.assertEqual(summary["spending"]["income"], 0)

    def test_linked_box_not_double_counted(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        self._post("/api/boxes", {"name": "Caixinha", "balance": 400, "account_id": acc["id"]})
        self._post("/api/boxes", {"name": "Solta", "balance": 300})

        summary = self.client.get("/api/summary").get_json()
        nw = summary["networth"]
        self.assertEqual(nw["accounts"], 1000)
        self.assertEqual(nw["boxes"], 300)  # só a solta conta
        self.assertEqual(nw["assets"], 1300)

        banks = summary["banks"]
        bank = next(b for b in banks["banks"] if b["id"] == acc["id"])
        self.assertEqual(bank["total"], 1000)
        self.assertEqual(bank["free"], 600)  # 1000 - 400 da caixinha vinculada
        self.assertEqual(len(bank["boxes"]), 1)

    def test_projection_with_financing_start_and_settings(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 2000})
        self._post("/api/recurring", {"name": "Aluguel", "amount": 510, "category": "Moradia", "active": True})
        self._post("/api/financings", {"name": "Notebook", "monthly_value": 200, "installments_total": 3, "paid": 0})
        self._post("/api/financings", {"name": "Moto", "monthly_value": 650, "installments_total": 20, "paid": 0, "start_date": "2026-09-01"})

        res = self.client.put("/api/settings/monthly_income", json={"value": 1000})
        self.assertEqual(res.status_code, 200)

        proj = self.client.get("/api/summary").get_json()["projection"]
        self.assertEqual(proj["income"], 1000)
        self.assertEqual(len(proj["months"]), 6)

        aug = proj["months"][0]
        self.assertEqual(aug["month"], "2026-08")
        self.assertEqual(aug["recurring"], 510)
        self.assertEqual(aug["financings"], 200)  # moto ainda não começou
        self.assertEqual(aug["total"], 710)

        sep = proj["months"][1]
        self.assertEqual(sep["month"], "2026-09")
        self.assertEqual(sep["financings"], 850)  # notebook 200 + moto 650

        # balanço acumulado com renda 1000: 2000 + 1000 - 710 = 2290 no 1º mês
        self.assertEqual(aug["balance"], 2290)
        # 2º mês: 510 recorrentes + 850 financiamentos = 1360
        self.assertEqual(sep["balance"], 2000 + 2 * 1000 - 710 - 1360)

    def test_unpay_purchase_reverts_and_removes_transaction(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        card = self._post("/api/cards", {"name": "Cartao", "limit": 5000})
        p = self._post("/api/card_purchases", {
            "card_id": card["id"], "date": "2026-08-01", "description": "Compra",
            "category": "Lazer", "amount": 300, "installments": 3,
        })
        self.client.post(f"/api/card_purchases/{p['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 1)

        res = self.client.post(f"/api/card_purchases/{p['id']}/unpay")
        self.assertEqual(res.status_code, 200)
        unp = res.get_json()
        self.assertEqual(unp["paid_count"], 0)
        self.assertFalse(unp["finished"])
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def test_unpay_financing_reverts_and_removes_transaction(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        f = self._post("/api/financings", {"name": "Notebook", "monthly_value": 200, "installments_total": 9, "paid": 1})
        self.client.post(f"/api/financings/{f['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 1)

        res = self.client.post(f"/api/financings/{f['id']}/unpay")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["paid"], 1)  # 2 pagos -> 1
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def test_meta(self):
        meta = self.client.get("/api/meta").get_json()
        self.assertEqual(meta["backend"], "local")
        self.assertIn("categories", meta)

    def test_append_import_dedup_and_references(self):
        xml = b"""<finance-export version="1.0">
          <accounts><item id="a1"><name>Nubank</name><institution>Nubank</institution><balance>100</balance></item></accounts>
          <cards><item id="c1"><name>Meu Cartao</name><limit>5000</limit></item></cards>
          <card_purchases><item><card_id>Meu Cartao</card_id><description>Compra X</description><amount>200</amount><installments>1</installments><date>2026-08-01</date></item></card_purchases>
          <recurring><item><name>Netflix</name><amount>39.9</amount><account_id>Nubank</account_id></item></recurring>
        </finance-export>"""

        for _ in range(2):  # importar 2x -> não pode duplicar
            res = self.client.post("/api/import", data={
                "file": (io.BytesIO(xml), "x.xml"),
                "mode": "append",
            }, content_type="multipart/form-data")
            self.assertEqual(res.status_code, 200, res.get_json())

        self.assertEqual(len(self.client.get("/api/accounts").get_json()), 1)
        self.assertEqual(len(self.client.get("/api/cards").get_json()), 1)

        purchases = self.client.get("/api/card_purchases").get_json()
        self.assertEqual(len(purchases), 1)
        card = self.client.get("/api/cards").get_json()[0]
        self.assertEqual(purchases[0]["card_id"], card["id"])

        recs = self.client.get("/api/recurring").get_json()
        acc = self.client.get("/api/accounts").get_json()[0]
        self.assertEqual(recs[0]["account_id"], acc["id"])
        self.assertEqual(recs[0]["amount"], 39.9)


    def test_validation_rejects_bad_input(self):
        # saldo negativo em conta é permitido (cheque especial)
        res = self.client.post("/api/accounts", json={"name": "X", "balance": -5})
        self.assertEqual(res.status_code, 201, res.get_json())

        # mas em caixinha não faz sentido
        res = self.client.post("/api/boxes", json={"name": "X", "balance": -5})
        self.assertEqual(res.status_code, 400, res.get_json())

        res = self.client.post("/api/accounts", json={"name": "X", "campo_inexistente": 1})
        self.assertEqual(res.status_code, 400, res.get_json())

        res = self.client.post("/api/accounts", json={"balance": 10})
        self.assertEqual(res.status_code, 400, res.get_json())

        res = self.client.post("/api/accounts", json={"name": "X", "balance": "abc"})
        self.assertEqual(res.status_code, 400, res.get_json())

    def test_recurring_pay_tracking_and_unpay(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 500})
        rec = self._post("/api/recurring", {"name": "Internet", "amount": 99.9, "account_id": acc["id"]})

        res = self.client.post(f"/api/recurring/{rec['id']}/pay", json={})
        self.assertEqual(res.status_code, 200)

        pays = self.client.get("/api/payments").get_json()
        self.assertEqual(len(pays), 1)
        self.assertEqual(pays[0]["kind"], "recurring")
        self.assertEqual(pays[0]["ref_id"], rec["id"])
        self.assertTrue(pays[0]["tx_id"])

        res = self.client.post(f"/api/recurring/{rec['id']}/unpay")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.client.get("/api/payments").get_json()), 0)
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def test_double_pay_same_month_blocked(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 500})
        rec = self._post("/api/recurring", {"name": "Netflix", "amount": 39.9, "account_id": acc["id"]})

        r1 = self.client.post(f"/api/recurring/{rec['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(f"/api/recurring/{rec['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(len(self.client.get("/api/payments").get_json()), 1)

    def test_delete_account_blocked_with_references_and_force(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        self._post("/api/boxes", {"name": "Reserva", "balance": 300, "account_id": acc["id"]})

        res = self.client.delete(f"/api/accounts/{acc['id']}")
        self.assertEqual(res.status_code, 409, res.get_json())

        res = self.client.delete(f"/api/accounts/{acc['id']}?force=1")
        self.assertEqual(res.status_code, 200, res.get_json())
        box = self.client.get("/api/boxes").get_json()[0]
        self.assertEqual(box["account_id"], "")
        self.assertEqual(len(self.client.get("/api/accounts").get_json()), 0)

    def test_delete_paid_purchase_blocked_and_force(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        card = self._post("/api/cards", {"name": "Cartao", "limit": 5000})
        p = self._post("/api/card_purchases", {
            "card_id": card["id"], "date": "2026-08-01", "description": "Compra",
            "amount": 300, "installments": 3,
        })
        self.client.post(f"/api/card_purchases/{p['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 1)

        res = self.client.delete(f"/api/card_purchases/{p['id']}")
        self.assertEqual(res.status_code, 409, res.get_json())

        res = self.client.delete(f"/api/card_purchases/{p['id']}?force=1")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(len(self.client.get("/api/payments").get_json()), 0)
        self.assertEqual(len(self.client.get("/api/transactions").get_json()), 0)

    def test_payment_audit_trail_for_card(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        card = self._post("/api/cards", {"name": "Cartao", "limit": 5000})
        p = self._post("/api/card_purchases", {
            "card_id": card["id"], "date": "2026-08-01", "description": "Ten",
            "amount": 300, "installments": 3,
        })
        self.client.post(f"/api/card_purchases/{p['id']}/pay", json={"account_id": acc["id"]})

        pays = self.client.get("/api/payments").get_json()
        self.assertEqual(len(pays), 1)
        self.assertEqual(pays[0]["kind"], "card")
        self.assertEqual(pays[0]["ref_id"], p["id"])
        self.assertEqual(pays[0]["amount"], 100.0)
        self.assertTrue(pays[0]["tx_id"])

        # Pagar de novo no mesmo mês é bloqueado
        res = self.client.post(f"/api/card_purchases/{p['id']}/pay", json={"account_id": acc["id"]})
        self.assertEqual(res.status_code, 400)

    def test_unknown_page_returns_404(self):
        res = self.client.get("/pagina-que-nao-existe")
        self.assertEqual(res.status_code, 404)
        res = self.client.get("/api/colecao-que-nao-existe")
        self.assertEqual(res.status_code, 404)

    def test_transfer_to_investment_keeps_quantity_precision(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        inv = self._post("/api/investments", {"name": "FII", "quantity": 0, "current_price": 0.31})

        res = self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "investment", "to_id": inv["id"],
            "amount": 1.00, "date": "2026-08-05",
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        inv_after = self._get("investments", inv["id"])
        # 1.00 / 0.31 = 3.22580645... não pode virar 3.23
        self.assertAlmostEqual(inv_after["quantity"], 3.22580645, places=7)

    def test_revert_transfer_uses_stored_units_not_current_price(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        inv = self._post("/api/investments", {"name": "FII", "quantity": 0, "current_price": 10})

        res = self.client.post("/api/transfers", json={
            "from_type": "account", "from_id": acc["id"],
            "to_type": "investment", "to_id": inv["id"],
            "amount": 150, "date": "2026-08-05",
        })
        self.assertEqual(res.status_code, 201, res.get_json())
        t = res.get_json()

        # O preço muda para 15 após a transferência (valorização).
        inv = self._get("investments", inv["id"])
        inv["current_price"] = 15
        self.client.put(f"/api/investments/{inv['id']}", json=inv)

        res = self.client.delete(f"/api/transfers/{t['id']}")
        self.assertEqual(res.status_code, 200)
        # O estorno deve remover as 15 cotas originais, não 150/15=10 cotas.
        inv_after = self._get("investments", inv["id"])
        self.assertAlmostEqual(inv_after["quantity"], 0.0, places=7)

    def test_negative_account_balance_allowed(self):
        acc = self._post("/api/accounts", {"name": "Cheque especial", "balance": -150.75})
        self.assertEqual(acc["balance"], -150.75)

    def test_payment_transaction_delete_blocked_and_force(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        rec = self._post("/api/recurring", {"name": "Internet", "amount": 99.9, "account_id": acc["id"]})
        self.client.post(f"/api/recurring/{rec['id']}/pay", json={})

        txs = self.client.get("/api/transactions").get_json()
        self.assertEqual(len(txs), 1)
        tx_id = txs[0]["id"]

        res = self.client.delete(f"/api/transactions/{tx_id}")
        self.assertEqual(res.status_code, 409, res.get_json())

        res = self.client.delete(f"/api/transactions/{tx_id}?force=1")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(len(self.client.get("/api/payments").get_json()), 0)

    def test_dashboard_bank_total_does_not_double_count_investments(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        self._post("/api/investments", {"name": "FII", "quantity": 10, "current_price": 30, "account_id": acc["id"]})

        banks = self.client.get("/api/summary").get_json()["banks"]["banks"]
        bank = next(b for b in banks if b["id"] == acc["id"])
        # o total do banco já é o saldo da conta (que inclui o investimento vinculado)
        self.assertEqual(bank["total"], 1000)

    def test_linked_investment_not_double_counted_in_networth(self):
        acc = self._post("/api/accounts", {"name": "Nubank", "balance": 1000})
        self._post("/api/investments", {"name": "MXRF11", "quantity": 10, "current_price": 30, "account_id": acc["id"]})

        nw = self.client.get("/api/summary").get_json()["networth"]
        # o investimento vinculado já está dentro do saldo da conta
        self.assertEqual(nw["accounts"], 1000)
        self.assertEqual(nw["investments"], 0)
        self.assertEqual(nw["assets"], 1000)

    def test_orphan_investment_counts_in_networth(self):
        self._post("/api/investments", {"name": "Cripto solta", "quantity": 2, "current_price": 50})
        nw = self.client.get("/api/summary").get_json()["networth"]
        self.assertEqual(nw["investments"], 100)

    def test_projection_includes_all_remaining_installments(self):
        acc = self._post("/api/accounts", {"name": "Conta", "balance": 1000})
        card = self._post("/api/cards", {"name": "Cartao", "limit": 5000, "due_day": 10})
        # 10x de 10.00, começando 2026-03; hoje é 2026-08, então ainda restam
        # parcelas em 08..12 (05/2026 já passou).
        p = self._post("/api/card_purchases", {
            "card_id": card["id"], "description": "Fini", "amount": 100,
            "installments": 10, "date": "2026-03-15",
        })
        self.assertEqual(p["paid_count"], 0)

        months = self.client.get("/api/summary").get_json()["projection"]["months"]
        # a projeção começa no mês atual (2026-08) e vai até 2027-01
        self.assertEqual(len(months), 6)
        # parcelas restantes (08..12) devem aparecer em TODOS os meses, não só no
        # mês da próxima parcela. O bug antigo mostrava apenas um mês.
        invoiced = [m["invoice"] for m in months]
        self.assertEqual(invoiced[0:5], [10.0, 10.0, 10.0, 10.0, 10.0])
        self.assertEqual(invoiced[5], 0.0)


class AIExtractTest(unittest.TestCase):
    """Testes do fluxo de leitura de extrato com IA (Gemini).

    A chamada de rede é substituída por um mock; não precisa de chave real.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="finance_ai_test_")
        store.reset_storage_for_tests(path=os.path.join(cls.tmp, "db.json"))
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        from app import config as cfg
        cls._orig_keyfile = cfg.GEMINI_KEY_FILE
        cls.keyfile = os.path.join(cls.tmp, "gemini_key.txt")
        cfg.GEMINI_KEY_FILE = cls.keyfile

    @classmethod
    def tearDownClass(cls):
        from app import config as cfg
        cfg.GEMINI_KEY_FILE = cls._orig_keyfile
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        store.reset_storage_for_tests(path=os.path.join(self.tmp, "db.json"))
        with open(self.keyfile, "w", encoding="utf-8") as fh:
            fh.write("fake-key")

    def _payload(self, text):
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def _extract(self, text):
        mocked = mock.Mock()
        mocked.status_code = 200
        mocked.json.return_value = self._payload(text)
        return mock.patch("requests.post", return_value=mocked)

    def test_extract_and_confirm_flow(self):
        with self._extract(
            '{"items": ['
            '{"date": "05/08/2026", "description": "PADARIA", "type": "expense", '
            '"amount": "R$ 12,50", "category": "alimentação"},'
            '{"date": "2026-08-03", "description": "SALÁRIO", "type": "income", '
            '"amount": "5.000,00", "category": "salário"}'
            "]}"
        ):
            res = self.client.post(
                "/api/ai/extract-statement",
                data={"image": (io.BytesIO(b"print"), "extrato.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(res.status_code, 200, res.get_json())
        items = res.get_json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["amount"], 12.5)
        self.assertEqual(items[0]["date"], "2026-08-05")
        self.assertEqual(items[0]["type"], "expense")
        self.assertEqual(items[1]["amount"], 5000.0)
        self.assertEqual(items[1]["type"], "income")

        acc = self.client.post("/api/accounts", json={"name": "Nubank", "balance": 0}).get_json()
        res = self.client.post(
            "/api/ai/confirm-statement",
            json={"items": items, "account_id": acc["id"]},
        )
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()["created"], 2)

    def test_extract_without_key_returns_400(self):
        os.remove(self.keyfile)
        res = self.client.post(
            "/api/ai/extract-statement",
            data={"image": (io.BytesIO(b"print"), "extrato.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("não configurada", res.get_json()["error"])

    def test_extract_api_error_returns_502(self):
        with mock.patch("requests.post") as mocked:
            mocked.return_value.status_code = 400
            mocked.return_value.text = "API key not valid"
            res = self.client.post(
                "/api/ai/extract-statement",
                data={"image": (io.BytesIO(b"print"), "extrato.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(res.status_code, 502)
        self.assertIn("erro da API Gemini", res.get_json()["error"])

    def test_confirm_rejects_invalid_account_and_empty(self):
        res = self.client.post("/api/ai/confirm-statement", json={"items": [], "account_id": ""})
        self.assertEqual(res.status_code, 400)

        res = self.client.post("/api/ai/confirm-statement", json={"items": [{"date": "2026-08-05", "description": "x", "amount": 1}], "account_id": "nao-existe"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("conta não encontrada", res.get_json()["error"])

    def test_parse_handles_br_formats(self):
        from app.ai import _normalize_items

        items = _normalize_items({"items": [
            {"date": "05/08/2026", "description": "A", "amount": "R$ 1.234,56", "type": "expense"},
            {"date": "05/08", "description": "B", "amount": "50", "type": "income"},
            {"date": "invalida", "description": "C", "amount": 10},
            {"date": "2026-08-05", "description": "D", "amount": "abc"},
        ]})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["amount"], 1234.56)
        self.assertEqual(items[1]["date"], "2026-08-05")


if __name__ == "__main__":
    unittest.main()

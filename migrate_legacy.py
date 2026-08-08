"""Converte o backup XML do sistema financeiro antigo (FinancialSystem)
para o modelo novo e acrescenta ao armazenamento atual.

É seguro rodar mais de uma vez: registros já existentes são ignorados
(não duplica) e referências entre coleções são resolvidas.

Uso:
    .venv\\Scripts\\python migrate_legacy.py "C:\\caminho\\backup.xml"
"""

import sys
import xml.etree.ElementTree as ET

from app import storage as store
from app import xml_utils

CATEGORY_MAP = {
    "Apartamento": "Moradia",
    "Spotify": "Lazer",
}


def fval(node, path, default=0.0):
    text = node.findtext(path)
    if text is None or str(text).strip() == "":
        return float(default)
    return float(text)


def main(path):
    tree = ET.parse(path)
    root = tree.getroot()

    backup_date = root.findtext(".//Information/BackupDate", "").strip()
    storage = store.get_storage()

    data = {col: [] for col in xml_utils.COLLECTION_ORDER}
    account_ids = {}

    # ---------------- Contas ----------------
    accounts = []
    for idx, acc in enumerate(root.findall(".//Accounts/Account"), start=1):
        bank = (acc.findtext("Bank") or "").strip()
        current = fval(acc, "CurrentAccount")
        acc_id = f"legacy-acc-{idx}"
        account_ids[bank] = acc_id
        accounts.append({
            "id": acc_id,
            "name": bank or "Conta",
            "institution": bank,
            "type": "Conta corrente" if bank == "Nubank" else "Carteira digital",
            "balance": current,
        })
    data["accounts"] = accounts

    # ---------------- Caixinhas / reservas ----------------
    emergency_fund = 0.0
    boxes = []
    for acc in root.findall(".//Accounts/Account"):
        emergency_fund += fval(acc, "EmergencyFund")
        for mb in acc.findall("MoneyBoxes/MoneyBox"):
            boxes.append({
                "name": (mb.findtext("Name") or "").strip(),
                "target": 0.0,
                "balance": fval(mb, "Balance"),
                "is_emergency": False,
            })
        for res in acc.findall("ReservedMoney/Reserve"):
            boxes.append({
                "name": (res.findtext("Category") or "Reserva").strip(),
                "target": 0.0,
                "balance": fval(res, "Amount"),
                "is_emergency": False,
            })

    goals = []
    for g in root.findall(".//Goals/Goal"):
        goals.append({
            "name": (g.findtext("Name") or "").strip(),
            "target": fval(g, "Target"),
        })

    merged_emergency = False
    for g in goals:
        is_emergency = g["name"] == "Reserva de Emergência"
        if is_emergency and not merged_emergency:
            boxes.append({
                "name": g["name"],
                "target": g["target"],
                "balance": emergency_fund,
                "is_emergency": True,
            })
            merged_emergency = True
        elif not is_emergency:
            boxes.append({
                "name": g["name"],
                "target": g["target"],
                "balance": 0.0,
                "is_emergency": False,
            })

    if not merged_emergency and emergency_fund:
        boxes.append({
            "name": "Reserva de Emergência",
            "target": 0.0,
            "balance": emergency_fund,
            "is_emergency": True,
        })

    data["boxes"] = boxes

    # ---------------- Investimentos ----------------
    for inv in root.findall(".//Investments/Investment"):
        ticker = (inv.findtext("Ticker") or "").strip()
        shares = fval(inv, "Shares")
        value = fval(inv, "CurrentValue")
        price = round(value / shares, 8) if shares else 0.0
        data["investments"].append({
            "name": ticker or "Ativo",
            "ticker": ticker,
            "type": "FII",
            "quantity": shares,
            "avg_price": price,
            "current_price": price,
        })

    # ---------------- Cartões de crédito ----------------
    invoice = fval(root, ".//CreditCard/CurrentInvoice")
    if invoice > 0:
        data["cards"].append({
            "id": "legacy-card-1",
            "name": "Cartão de crédito",
            "brand": "",
            "limit": 0,
            "due_day": 10,
        })
        data["card_purchases"].append({
            "card_id": "legacy-card-1",
            "date": backup_date,
            "description": "Fatura atual",
            "category": "Cartão de crédito",
            "amount": invoice,
            "installments": 1,
            "paid_count": 0,
            "finished": False,
        })

    # ---------------- Financiamentos (empréstimos) ----------------
    for loan in root.findall(".//Loans/Loan"):
        name = (loan.findtext("Name") or "").strip()
        monthly = fval(loan, "InstallmentValue")
        paid = int(fval(loan, "PaidInstallments"))
        remaining = int(fval(loan, "RemainingInstallments"))
        total_inst = int(fval(loan, "TotalInstallments", default=paid + remaining))
        total = fval(loan, "TotalValue", default=monthly * total_inst)
        data["financings"].append({
            "name": name,
            "category": "Transporte" if "Fan" in name else "Outros",
            "total": total,
            "monthly_value": monthly,
            "installments_total": total_inst,
            "paid": paid,
            "due_day": 5 if "Fan" in name else 10,
            "start_date": "",
        })

    # ---------------- Contas recorrentes (despesas fixas) ----------------
    loan_names = {d["name"] for d in data["financings"]}
    for exp in root.findall(".//FixedExpenses/Expense"):
        name = (exp.findtext("Name") or "").strip()
        if name in loan_names:
            continue
        data["recurring"].append({
            "name": name,
            "category": CATEGORY_MAP.get(name, "Outros"),
            "amount": fval(exp, "Value"),
            "frequency": "Mensal",
            "due_day": 10,
            "account_id": "",
            "active": True,
        })

    counts = xml_utils.append_import(storage, data)

    # ---------------- Snapshot de patrimônio ----------------
    from app.services import compute_networth

    nw = compute_networth(storage)["networth"]
    snap = xml_utils.append_import(storage, {
        "networth": [{"date": backup_date or "2026-08-07", "total": nw}]
    })
    counts["networth"] = snap["networth"]

    print("Importação concluída:")
    for key, value in counts.items():
        if value.get("added") or value.get("updated"):
            print(f"  {key}: +{value['added']} adicionados, "
                  f"~{value['updated']} atualizados, "
                  f"={value['skipped']} já existiam")
    print(f"  Patrimônio líquido (ativos - dívidas): R$ {nw:,.2f}")
    print(f"  Armazenamento: {storage.describe()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Informe o caminho do XML antigo, ex.:")
        print('  python migrate_legacy.py "C:\\Users\\SICKE\\Downloads\\financeiro_backup_2026-08-07.xml"')
        sys.exit(1)
    main(sys.argv[1])

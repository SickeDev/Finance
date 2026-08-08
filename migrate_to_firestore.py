"""Sobe os dados do arquivo local (data/local_db.json) para o Firebase Firestore.

Preserva os mesmos IDs, então todos os vínculos (caixinhas por conta,
pagamentos, financiamentos etc.) continuam funcionando. Pode rodar de novo:
documentos existentes são sobrescritos com o mesmo conteúdo.

Requer credentials/serviceAccountKey.json ou a variável de ambiente
FIREBASE_SERVICE_ACCOUNT_JSON com o JSON da conta de serviço.
"""

import json
import os
import sys

from app import config
from app import storage as store


def main():
    if not (config.FIREBASE_SERVICE_ACCOUNT_JSON or os.path.exists(config.CREDENTIALS_PATH)):
        print(
            "ERRO: coloque o arquivo credentials/serviceAccountKey.json "
            "(ou defina a variável FIREBASE_SERVICE_ACCOUNT_JSON)."
        )
        return 1

    try:
        fs = store.FirestoreStorage()
    except Exception as exc:  # pragma: no cover
        print(f"ERRO ao conectar no Firebase: {exc}")
        return 1

    with open(config.LOCAL_DB_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    total = 0
    for col in config.COLLECTIONS:
        docs = data.get(col) or {}
        if not docs:
            continue
        for doc in docs.values():
            doc = dict(doc)
            doc_id = doc.get("id")
            if not doc_id:
                continue
            fs.update(col, doc_id, doc)
            total += 1
        print(f"  {col}: {len(docs)} docs")

    project = getattr(getattr(fs, "db", None), "project", "?")
    print(f"\nOK: {total} documentos enviados ao Firestore (projeto {project}).")


if __name__ == "__main__":
    sys.exit(main())

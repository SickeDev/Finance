"""Rotas de páginas e API.

Camada HTTP fina: lê o request, chama a lógica de domínio (app/services.py)
e devolve JSON. Erros de regra de negócio (ValueError) viram 4xx com mensagem.
"""

import io

from flask import abort, jsonify, render_template, request, send_file

from . import ai
from . import config
from . import market
from . import services
from . import storage as store
from . import xlsx_utils
from . import xml_utils

PAGES = {
    "contas": "accounts.html",
    "cartoes": "cards.html",
    "caixinhas": "boxes.html",
    "investimentos": "investments.html",
    "financiamentos": "financings.html",
    "recorrentes": "recurring.html",
    "contas-a-pagar": "contas_a_pagar.html",
    "transacoes": "transactions.html",
    "movimentacoes": "transfers.html",
    "dados": "data.html",
}

CRUD_COLLECTIONS = [
    "accounts",
    "cards",
    "card_purchases",
    "boxes",
    "investments",
    "financings",
    "recurring",
]


def _body():
    return request.get_json(silent=True) or {}


def _err(message, status):
    return jsonify({"error": message}), status


def init_app(app):

    # ------------------------------------------------------------- páginas

    @app.get("/")
    def page_dashboard():
        storage = store.get_storage()
        services.record_daily_snapshot(storage)
        return render_template("dashboard.html")

    @app.get("/<page>")
    def page_generic(page):
        if page not in PAGES:
            abort(404)
        return render_template(PAGES[page])

    # ------------------------------------------------------------- meta / resumo

    @app.get("/api/meta")
    def api_meta():
        storage = store.get_storage()
        return jsonify(
            {
                "categories": config.CATEGORIES,
                "storage": storage.describe(),
                "storage_error": store.storage_error(),
                "backend": storage.backend,
            }
        )

    @app.get("/api/summary")
    def api_summary():
        storage = store.get_storage()
        services.record_daily_snapshot(storage)
        return jsonify(services.build_summary(storage))

    # ------------------------------------------------------------- CRUD genérico

    def make_crud(collection):
        base = f"/api/{collection}"

        def api_list():
            return jsonify(store.get_storage().list(collection))

        def api_create():
            storage = store.get_storage()
            try:
                if collection == "card_purchases":
                    doc = services.create_card_purchase(storage, _body())
                else:
                    doc = services.validate_doc(collection, _body())
                    doc["id"] = store.new_id()
                    storage.insert(collection, doc)
            except ValueError as exc:
                return _err(str(exc), 400)
            return jsonify(doc), 201

        def api_update(doc_id):
            storage = store.get_storage()
            if collection == "card_purchases":
                try:
                    doc = services.update_card_purchase(storage, doc_id, _body())
                except ValueError as exc:
                    if str(exc) == "não encontrado":
                        return _err(str(exc), 404)
                    return _err(str(exc), 400)
                return jsonify(doc)
            existing = storage.get(collection, doc_id)
            if existing is None:
                return _err("não encontrado", 404)
            try:
                patch = services.validate_doc(collection, _body(), partial=True)
            except ValueError as exc:
                return _err(str(exc), 400)
            existing.update(patch)
            existing["id"] = doc_id
            storage.update(collection, doc_id, existing)
            return jsonify(existing)

        def api_delete(doc_id):
            force = request.args.get("force") in ("1", "true", "True")
            try:
                services.delete_doc(store.get_storage(), collection, doc_id, force=force)
            except services.ReferenceBlocked as exc:
                return _err(str(exc), 409)
            except ValueError as exc:
                return _err(str(exc), 404)
            return jsonify({"ok": True})

        app.add_url_rule(base, f"{collection}_list", api_list, methods=["GET"])
        app.add_url_rule(base, f"{collection}_create", api_create, methods=["POST"])
        app.add_url_rule(f"{base}/<doc_id>", f"{collection}_update", api_update, methods=["PUT"])
        app.add_url_rule(f"{base}/<doc_id>", f"{collection}_delete", api_delete, methods=["DELETE"])

    for col in CRUD_COLLECTIONS:
        make_crud(col)

    # ------------------------------------------------------------- transações
    # Transações manuais têm origem/destino (conta ou caixinha) e movem o
    # saldo de verdade, então usam endpoints próprios (não o CRUD genérico).

    @app.get("/api/transactions")
    def api_list_transactions():
        return jsonify(store.get_storage().list("transactions"))

    @app.post("/api/transactions")
    def api_create_transaction():
        try:
            doc = services.create_transaction(store.get_storage(), _body())
        except ValueError as exc:
            return _err(str(exc), 400)
        return jsonify(doc), 201

    @app.put("/api/transactions/<doc_id>")
    def api_update_transaction(doc_id):
        try:
            doc = services.update_transaction(store.get_storage(), doc_id, _body())
        except ValueError as exc:
            if str(exc) == "não encontrado":
                return _err(str(exc), 404)
            return _err(str(exc), 400)
        return jsonify(doc)

    @app.delete("/api/transactions/<doc_id>")
    def api_delete_transaction(doc_id):
        force = request.args.get("force") in ("1", "true", "True")
        try:
            services.delete_doc(store.get_storage(), "transactions", doc_id, force=force)
        except services.ReferenceBlocked as exc:
            return _err(str(exc), 409)
        except ValueError as exc:
            return _err(str(exc), 404)
        return jsonify({"ok": True})

    # ------------------------------------------------------------- configurações

    @app.get("/api/settings")
    def api_get_settings():
        storage = store.get_storage()
        return jsonify(
            {s.get("key"): s.get("value") for s in storage.list("settings") if s.get("key")}
        )

    @app.put("/api/settings/<key>")
    def api_put_setting(key):
        if not key or len(key) > 60:
            return _err("chave inválida", 400)
        body = _body()
        value = body.get("value", body.get(key, 0))
        try:
            value = services.validate_doc("settings", {"key": key, "value": value})["value"]
        except ValueError as exc:
            return _err(str(exc), 400)
        storage = store.get_storage()
        storage.update("settings", key, {"id": key, "key": key, "value": value})
        return jsonify({"ok": True, "key": key, "value": value})

    # ------------------------------------------------------------- reajustes

    @app.get("/api/adjustments")
    def api_list_adjustments():
        rows = store.get_storage().list("adjustments")
        return jsonify(sorted(rows, key=lambda a: a.get("date", ""), reverse=True))

    @app.post("/api/adjustments")
    def api_create_adjustment():
        try:
            doc = services.apply_adjustment(store.get_storage(), _body())
        except ValueError as exc:
            if str(exc) == "não encontrado":
                return _err(str(exc), 404)
            return _err(str(exc), 400)
        return jsonify(doc), 201

    # ------------------------------------------------------------- movimentações

    @app.get("/api/transfers")
    def api_list_transfers():
        transfers = store.get_storage().list("transfers")
        return jsonify(sorted(transfers, key=lambda t: t.get("date", ""), reverse=True))

    @app.post("/api/transfers")
    def api_create_transfer():
        try:
            doc = services.create_transfer(store.get_storage(), _body())
        except ValueError as exc:
            return _err(str(exc), 400)
        return jsonify(doc), 201

    @app.delete("/api/transfers/<transfer_id>")
    def api_delete_transfer(transfer_id):
        try:
            services.delete_transfer(store.get_storage(), transfer_id)
        except ValueError as exc:
            return _err(str(exc), 404)
        return jsonify({"ok": True})

    # ------------------------------------------------------------- pagamentos

    @app.get("/api/payments")
    def api_list_payments():
        storage = store.get_storage()
        rows = storage.list("payments")
        kind = request.args.get("kind")
        ref_id = request.args.get("ref_id")
        if kind:
            rows = [p for p in rows if p.get("kind") == kind]
        if ref_id:
            rows = [p for p in rows if p.get("ref_id") == ref_id]
        return jsonify(sorted(rows, key=lambda p: (p.get("date", ""), p.get("id", ""))))

    def pay_route(collection, doc_id, fn):
        body = _body()
        try:
            doc = fn(store.get_storage(), doc_id, body.get("account_id") or "", body.get("date"))
        except ValueError as exc:
            if str(exc) == "não encontrado":
                return _err(str(exc), 404)
            return _err(str(exc), 400)
        return jsonify(doc)

    @app.post("/api/card_purchases/<purchase_id>/pay")
    def api_pay_purchase(purchase_id):
        return pay_route("card_purchases", purchase_id, services.pay_card_purchase)

    @app.post("/api/card_purchases/<purchase_id>/unpay")
    def api_unpay_purchase(purchase_id):
        try:
            doc = services.unpay_card_purchase(store.get_storage(), purchase_id)
        except ValueError as exc:
            return _err(str(exc), 404)
        return jsonify(doc)

    @app.post("/api/financings/<fin_id>/pay")
    def api_pay_financing(fin_id):
        return pay_route("financings", fin_id, services.pay_financing)

    @app.post("/api/financings/<fin_id>/unpay")
    def api_unpay_financing(fin_id):
        try:
            doc = services.unpay_financing(store.get_storage(), fin_id)
        except ValueError as exc:
            return _err(str(exc), 404)
        return jsonify(doc)

    @app.post("/api/recurring/<rec_id>/pay")
    def api_pay_recurring(rec_id):
        return pay_route("recurring", rec_id, services.pay_recurring)

    @app.post("/api/recurring/<rec_id>/unpay")
    def api_unpay_recurring(rec_id):
        try:
            doc = services.unpay_recurring(store.get_storage(), rec_id)
        except ValueError as exc:
            return _err(str(exc), 404)
        return jsonify(doc)

    # ------------------------------------------------------------- investimentos (mercado)

    @app.post("/api/investments/refresh-prices")
    def api_refresh_investment_prices():
        storage = store.get_storage()
        try:
            results = market.refresh_investment_prices(storage)
        except Exception as exc:  # pragma: no cover
            app.logger.exception("Falha ao atualizar cotações")
            return _err(f"falha ao consultar o mercado: {exc}", 502)
        ok = sum(1 for r in results if r.get("ok"))
        fail = len(results) - ok
        return jsonify({"ok": True, "updated": ok, "failed": fail, "results": results})

    @app.get("/api/market/dividends")
    def api_market_dividends():
        storage = store.get_storage()
        return jsonify(
            {
                "monthly": market.total_dividend_estimate(storage),
                "items": [
                    {
                        "id": i["id"],
                        "name": i.get("name", ""),
                        "ticker": i.get("ticker", ""),
                        "quantity": float(i.get("quantity", 0)),
                        "dividend_monthly": float(i.get("dividend_monthly", 0)),
                        "estimate": market.investment_dividend_estimate(i),
                    }
                    for i in storage.list("investments")
                ],
            }
        )

    # ------------------------------------------------------------- IA (extrato bancário)

    @app.get("/api/ai/status")
    def api_ai_status():
        return jsonify(
            {
                "configured": ai.is_configured(),
                "model": ai.model_name(),
                "provider": ai.provider_name(),
            }
        )

    @app.put("/api/ai/key")
    def api_ai_set_key():
        body = _body()
        key = str(body.get("key") or "").strip()
        if not key or len(key) > 200:
            return _err("chave inválida", 400)
        try:
            ai.save_key(key)
        except OSError as exc:
            return _err(f"não foi possível salvar a chave: {exc}", 500)
        return jsonify({"ok": True, "configured": True})

    @app.post("/api/ai/extract-statement")
    def api_ai_extract_statement():
        return _ai_extract(ai.extract_statement)

    @app.post("/api/ai/extract-receipt")
    def api_ai_extract_receipt():
        return _ai_extract(ai.extract_receipt)

    def _ai_extract(extractor):
        file = request.files.get("image") or request.files.get("file")
        if file is None:
            return _err("imagem não enviada", 400)
        data = file.read()
        if not data:
            return _err("arquivo vazio", 400)
        if not ai.is_configured():
            return _err(
                "IA não configurada. Defina a variável de ambiente da chave "
                "(OPENAI_API_KEY ou GEMINI_API_KEY) ou preencha a chave na "
                "página Dados.",
                400,
            )
        try:
            items = extractor(data, filename=file.filename or "")
        except ai.AIError as exc:
            return _err(str(exc), 502)
        return jsonify({"ok": True, "items": items})

    @app.post("/api/ai/confirm-statement")
    def api_ai_confirm_statement():
        storage = store.get_storage()
        body = _body()
        items = body.get("items") or []
        entity_type = (body.get("entity_type") or "account")
        entity_id = body.get("entity_id") or body.get("account_id") or ""
        if not isinstance(items, list) or not items:
            return _err("nenhum item para lançar", 400)
        if entity_id:
            coll = config.ENTITY_TYPES.get(entity_type)
            if coll not in ("accounts", "boxes") or storage.get(coll, entity_id) is None:
                return _err("entidade não encontrada", 400)

        created = 0
        for raw in items:
            amount = abs(float(raw.get("amount") or 0))
            if amount <= 0:
                continue
            try:
                payload = {
                    "date": raw.get("date") or services.today().isoformat(),
                    "description": raw.get("description") or "Transação (IA)",
                    "category": raw.get("category") or "Outros",
                    "amount": amount,
                    "type": raw.get("type") in ("income", "transfer")
                    and "income"
                    or "expense",
                    "method": raw.get("method") or "Pix",
                }
                if entity_id:
                    payload["entity_type"] = entity_type
                    payload["entity_id"] = entity_id
                services.create_transaction(storage, payload, allow_overdraft=True)
                created += 1
            except ValueError as exc:
                return _err(f"item inválido: {exc}", 400)

        return jsonify({"ok": True, "created": created})

    # ------------------------------------------------------------- import / export

    @app.get("/api/export")
    def api_export():
        storage = store.get_storage()
        xml_string = xml_utils.build_export_xml(storage)
        filename = f"finance_export_{services.today().isoformat()}.xml"
        return send_file(
            io.BytesIO(xml_string.encode("utf-8")),
            mimetype="application/xml",
            as_attachment=True,
            download_name=filename,
        )

    @app.post("/api/import")
    def api_import():
        file = request.files.get("file")
        if file is None:
            return _err("arquivo não enviado", 400)
        mode = request.form.get("mode", "append")
        storage = store.get_storage()
        filename = (file.filename or "").lower()
        is_xlsx = filename.endswith(".xlsx") or filename.endswith(".xlsm")
        try:
            data = (
                xlsx_utils.parse_import_xlsx(file.read())
                if is_xlsx
                else xml_utils.parse_import_xml(file.read())
            )
        except Exception as exc:
            fmt = "XLSX" if is_xlsx else "XML"
            return _err(f"{fmt} inválido: {exc}", 400)

        if mode == "replace":
            counts = {}
            for col, docs in data.items():
                for d in docs:
                    if not d.get("id"):
                        d["id"] = store.new_id()
                storage.replace_all(col, docs)
                counts[col] = {"total": len(docs)}
            return jsonify({"ok": True, "mode": mode, "counts": counts})

        counts = xml_utils.append_import(storage, data)
        return jsonify({"ok": True, "mode": mode, "counts": counts})

    @app.post("/api/reset")
    def api_reset():
        store.get_storage().reset_all()
        return jsonify({"ok": True})

    # ------------------------------------------------------------- erros

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return _err("não encontrado", 404)
        return ("<h1>404 · Página não encontrada</h1><p><a href='/'>Voltar ao início</a></p>", 404)

    @app.errorhandler(405)
    def method_not_allowed(_):
        return _err("método não permitido", 405)

    @app.errorhandler(Exception)
    def on_error(exc):
        app.logger.exception("Erro não tratado")
        if request.path.startswith("/api/"):
            return _err("erro interno do servidor", 500)
        return ("<h1>500 · Erro interno</h1>", 500)

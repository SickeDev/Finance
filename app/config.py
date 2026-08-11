import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials", "serviceAccountKey.json")

# Em deploy (Render etc.) não há arquivos locais: a chave do Firebase pode ser
# colada na variável de ambiente FIREBASE_SERVICE_ACCOUNT_JSON (o JSON inteiro).
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

DATA_DIR = os.path.join(BASE_DIR, "data")
LOCAL_DB_PATH = os.path.join(DATA_DIR, "local_db.json")

EXPORT_DIR = os.path.join(BASE_DIR, "exports")

# Chave da API do Google Gemini (IA de leitura de extrato). Pode ser informada
# pela variável de ambiente GEMINI_API_KEY ou no arquivo credentials/gemini_key.txt.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MODEL_FALLBACKS = os.environ.get(
    "GEMINI_MODEL_FALLBACKS",
    "gemini-flash-latest,gemini-flash-lite-latest,gemini-2.5-flash",
).split(",")
GEMINI_KEY_FILE = os.path.join(BASE_DIR, "credentials", "gemini_key.txt")

COLLECTIONS = [
    "accounts",
    "cards",
    "card_purchases",
    "boxes",
    "investments",
    "financings",
    "recurring",
    "transactions",
    "transfers",
    "payments",
    "adjustments",
    "networth",
    "settings",
]

CATEGORIES = [
    "Moradia",
    "Alimentação",
    "Transporte",
    "Lazer",
    "Saúde",
    "Educação",
    "Cartão de crédito",
    "Investimentos",
    "Salário",
    "Freela",
    "Outros",
]

# Mapeia o "tipo de entidade" usado em transferências para a coleção real.
ENTITY_TYPES = {
    "account": "accounts",
    "box": "boxes",
    "investment": "investments",
}

# ---------------------------------------------------------------------------
# Schemas de validação da API
# ---------------------------------------------------------------------------
# Cada coleção define os campos aceitos (whitelist), os obrigatórios e os
# tipos/limites de cada campo. A validação fica em app/services.py.

SCHEMA = {
    "accounts": {
        "required": ("name",),
        "fields": {
            "name": {"type": "str", "max": 120},
            "institution": {"type": "str", "max": 120},
            "type": {"type": "str", "max": 40},
            "balance": {"type": "money", "min": -10**15},
            "color": {"type": "str", "max": 20},
            "note": {"type": "str", "max": 500},
        },
    },
    "cards": {
        "required": ("name",),
        "fields": {
            "name": {"type": "str", "max": 120},
            "brand": {"type": "str", "max": 40},
            "limit": {"type": "money"},
            "closing_day": {"type": "int", "min": 1, "max": 31},
            "due_day": {"type": "int", "min": 1, "max": 31},
        },
    },
    "card_purchases": {
        "required": ("card_id", "description", "amount"),
        "fields": {
            "card_id": {"type": "str", "max": 64},
            "date": {"type": "date"},
            "description": {"type": "str", "max": 200},
            "category": {"type": "str", "max": 60},
            "amount": {"type": "money"},
            "installments": {"type": "int", "min": 1, "max": 240},
            "paid_count": {"type": "int", "min": 0},
            "finished": {"type": "bool"},
        },
    },
    "boxes": {
        "required": ("name",),
        "fields": {
            "name": {"type": "str", "max": 120},
            "target": {"type": "money"},
            "balance": {"type": "money"},
            "is_emergency": {"type": "bool"},
            "account_id": {"type": "str", "max": 64},
            "note": {"type": "str", "max": 500},
        },
    },
    "investments": {
        "required": ("name",),
        "fields": {
            "name": {"type": "str", "max": 120},
            "type": {"type": "str", "max": 40},
            "ticker": {"type": "str", "max": 20},
            "quantity": {"type": "number"},
            "avg_price": {"type": "money"},
            "current_price": {"type": "money"},
            "account_id": {"type": "str", "max": 64},
            "dividend_monthly": {"type": "money"},
            "quote_date": {"type": "date"},
            "quote_symbol": {"type": "str", "max": 40},
        },
    },
    "financings": {
        "required": ("name", "monthly_value", "installments_total"),
        "fields": {
            "name": {"type": "str", "max": 120},
            "category": {"type": "str", "max": 60},
            "total": {"type": "money"},
            "monthly_value": {"type": "money"},
            "installments_total": {"type": "int", "min": 1, "max": 600},
            "paid": {"type": "int", "min": 0},
            "due_day": {"type": "int", "min": 1, "max": 31},
            "start_date": {"type": "date"},
            "note": {"type": "str", "max": 500},
        },
    },
    "recurring": {
        "required": ("name", "amount"),
        "fields": {
            "name": {"type": "str", "max": 120},
            "category": {"type": "str", "max": 60},
            "amount": {"type": "money"},
            "frequency": {"type": "str", "max": 30},
            "due_day": {"type": "int", "min": 1, "max": 31},
            "account_id": {"type": "str", "max": 64},
            "active": {"type": "bool"},
            "note": {"type": "str", "max": 500},
        },
    },
    "transactions": {
        "required": ("date", "description", "amount", "type"),
        "fields": {
            "date": {"type": "date"},
            "description": {"type": "str", "max": 200},
            "category": {"type": "str", "max": 60},
            "amount": {"type": "money"},
            "type": {"type": "enum", "values": ("income", "expense", "transfer")},
            "account_id": {"type": "str", "max": 64},
            "entity_type": {"type": "enum", "values": ("", "account", "box")},
            "entity_id": {"type": "str", "max": 64},
            "method": {"type": "str", "max": 40},
            "card_id": {"type": "str", "max": 64},
            "financing_id": {"type": "str", "max": 64},
            "recurring_id": {"type": "str", "max": 64},
            "transfer_id": {"type": "str", "max": 64},
        },
    },
    "transfers": {
        "required": ("from_type", "from_id", "to_type", "to_id", "amount"),
        "fields": {
            "date": {"type": "date"},
            "description": {"type": "str", "max": 200},
            "amount": {"type": "money"},
            "from_type": {"type": "enum", "values": ("account", "box", "investment")},
            "from_id": {"type": "str", "max": 64},
            "to_type": {"type": "enum", "values": ("account", "box", "investment")},
            "to_id": {"type": "str", "max": 64},
        },
    },
    "payments": {
        "required": ("kind", "ref_id", "date", "amount"),
        "fields": {
            "kind": {"type": "enum", "values": ("card", "financing", "recurring")},
            "ref_id": {"type": "str", "max": 64},
            "date": {"type": "date"},
            "amount": {"type": "money"},
            "account_id": {"type": "str", "max": 64},
            "tx_id": {"type": "str", "max": 64},
            "description": {"type": "str", "max": 200},
        },
    },
    "networth": {
        "required": ("date", "total"),
        "fields": {
            "date": {"type": "date"},
            "total": {"type": "money"},
        },
    },
    "adjustments": {
        "required": ("entity_type", "entity_id", "field", "old_value", "new_value"),
        "fields": {
            "entity_type": {"type": "str", "max": 40},
            "entity_id": {"type": "str", "max": 64},
            "entity_name": {"type": "str", "max": 120},
            "field": {"type": "str", "max": 40},
            "old_value": {"type": "number"},
            "new_value": {"type": "number"},
            "date": {"type": "date"},
            "note": {"type": "str", "max": 300},
        },
    },
    "settings": {
        "required": ("key",),
        "fields": {
            "key": {"type": "str", "max": 60},
            "value": {"type": "money"},
        },
    },
}

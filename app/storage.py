"""Camada de armazenamento abstrata.

- LocalStorage: arquivo JSON local (fallback, funciona sem credenciais).
- FirestoreStorage: Firebase Firestore, usado quando existem credenciais
  em credentials/serviceAccountKey.json.
"""

import json
import os
import tempfile
import threading
import uuid

from . import config


def new_id():
    return uuid.uuid4().hex


class LocalStorage:
    backend = "local"

    def __init__(self, path=None):
        self.path = path or config.LOCAL_DB_PATH
        self._lock = threading.RLock()
        self._db = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(self.path), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._db, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ---- API ----

    def list(self, collection):
        return list(self._db.get(collection, {}).values())

    def get(self, collection, doc_id):
        return self._db.get(collection, {}).get(doc_id)

    def insert(self, collection, doc):
        with self._lock:
            self._db.setdefault(collection, {})[doc["id"]] = doc
            self._save()

    def update(self, collection, doc_id, doc):
        with self._lock:
            self._db.setdefault(collection, {})[doc_id] = doc
            self._save()

    def apply_updates(self, pairs):
        """Atualiza vários documentos em uma única escrita atômica."""
        with self._lock:
            for col, doc in pairs:
                self._db.setdefault(col, {})[doc["id"]] = doc
            self._save()

    def delete(self, collection, doc_id):
        with self._lock:
            if self._db.get(collection, {}).pop(doc_id, None) is not None:
                self._save()

    def delete_many(self, collection, ids):
        with self._lock:
            coll = self._db.get(collection)
            if not coll:
                return
            changed = False
            for doc_id in ids:
                if coll.pop(doc_id, None) is not None:
                    changed = True
            if changed:
                self._save()

    def replace_all(self, collection, docs):
        with self._lock:
            self._db[collection] = {d["id"]: d for d in docs}
            self._save()

    def reset_all(self):
        with self._lock:
            self._db = {}
            self._save()

    def describe(self):
        return "Arquivo local: data/local_db.json"


class FirestoreStorage:
    backend = "firestore"

    def __init__(self):
        import json
        import firebase_admin
        from firebase_admin import credentials as fbc
        from firebase_admin import firestore

        if not firebase_admin._apps:
            if config.FIREBASE_SERVICE_ACCOUNT_JSON:
                cred = fbc.Certificate(json.loads(config.FIREBASE_SERVICE_ACCOUNT_JSON))
            else:
                env_cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
                if env_cred and os.path.exists(env_cred):
                    cred = fbc.Certificate(env_cred)
                else:
                    cred = fbc.Certificate(config.CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()

    def _coll(self, name):
        return self.db.collection(name)

    def _doc(self, collection, doc_id):
        snap = self._coll(collection).document(doc_id).get()
        if not snap.exists:
            return None
        return {**snap.to_dict(), "id": snap.id}

    # ---- API ----

    def list(self, collection):
        return [
            {**d.to_dict(), "id": d.id}
            for d in self._coll(collection).stream()
        ]

    def get(self, collection, doc_id):
        return self._doc(collection, doc_id)

    def insert(self, collection, doc):
        self._coll(collection).document(doc["id"]).set(doc)

    def update(self, collection, doc_id, doc):
        self._coll(collection).document(doc_id).set(doc)

    def apply_updates(self, pairs):
        batch = self.db.batch()
        for col, doc in pairs:
            batch.set(self._coll(col).document(doc["id"]), doc)
        batch.commit()

    def delete(self, collection, doc_id):
        self._coll(collection).document(doc_id).delete()

    def delete_many(self, collection, ids):
        if not ids:
            return
        batch = self.db.batch()
        for doc_id in ids:
            batch.delete(self._coll(collection).document(doc_id))
        batch.commit()

    def replace_all(self, collection, docs):
        batch = self.db.batch()
        for d in docs:
            batch.set(self._coll(collection).document(d["id"]), d)
        batch.commit()

    def reset_all(self):
        for col in config.COLLECTIONS:
            refs = [d.reference for d in self._coll(col).list_documents()]
            if not refs:
                continue
            batch = self.db.batch()
            for r in refs:
                batch.delete(r)
            batch.commit()

    def describe(self):
        return "Firebase Firestore (nuvem)"


_storage = None
_storage_error = None


def get_storage():
    global _storage, _storage_error
    if _storage is not None:
        return _storage

    cred_path = config.CREDENTIALS_PATH
    env_cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    has_file_cred = bool(env_cred and os.path.exists(env_cred)) or os.path.exists(cred_path)

    if config.FIREBASE_SERVICE_ACCOUNT_JSON or has_file_cred:
        try:
            _storage = FirestoreStorage()
            return _storage
        except Exception as exc:  # pragma: no cover
            _storage_error = f"{type(exc).__name__}: {exc}"

    _storage = LocalStorage()
    return _storage


def storage_error():
    return _storage_error


def reset_storage_for_tests(path=None):
    """Força uso de um armazenamento local específico (usado nos testes)."""
    global _storage
    _storage = LocalStorage(path=path)
    return _storage

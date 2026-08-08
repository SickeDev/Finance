import os
import threading
import webbrowser

from app import create_app

app = create_app()


def _open_browser():
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    # Só abre o navegador quando rodando local (não em deploy na nuvem).
    if host == "127.0.0.1":
        threading.Timer(1.2, _open_browser).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)

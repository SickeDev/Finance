import os

from flask import Flask

from . import config


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(config.BASE_DIR, "templates"),
        static_folder=os.path.join(config.BASE_DIR, "static"),
    )
    app.config["JSON_SORT_KEYS"] = False

    from . import routes

    routes.init_app(app)
    return app

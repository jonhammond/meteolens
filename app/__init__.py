"""Application factory."""

from flask import Flask

from app.config import Config, ConfigError
from app.routes import bp

__all__ = ["create_app", "ConfigError"]


def create_app(env=None):
    """Build the Flask app, validating the environment before serving anything.

    Raises ConfigError if a required variable is unset, so a misconfigured
    deploy fails at boot instead of at the first request.
    """
    app = Flask(__name__)
    app.config["METEOLENS"] = Config(env)
    app.register_blueprint(bp)
    return app

"""Environment configuration with fail-fast validation.

No hardcoded fallbacks for secrets: a missing required variable raises at
startup rather than letting the app run in a half-configured state.
"""

import logging
import os
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

REQUIRED_VARS = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "POWERBI_PUSH_URL",
    "INGEST_TOKEN",
)

OPTIONAL_VARS = ("POWERBI_EMBED_URL", "OPEN_METEO_API_KEY")


class ConfigError(RuntimeError):
    """Raised when the environment is missing something the app requires."""


def _is_publish_to_web_url(value):
    """True only for a Power BI "Publish to web" URL, which needs no viewer sign-in.

    The secure-embed URL (app.powerbi.com/reportEmbed?...&autoAuth=true&ctid=...)
    looks similar but forces every visitor to authenticate against the tenant,
    which renders as a sign-in wall on a public page.
    """
    parts = urlparse(value)
    return (
        parts.scheme == "https"
        and parts.netloc == "app.powerbi.com"
        and parts.path.rstrip("/") == "/view"
        and bool(parse_qs(parts.query).get("r", [""])[0])
    )


class Config:
    """Validated view of the process environment.

    Attributes are set from REQUIRED_VARS/OPTIONAL_VARS, so `Config.SUPABASE_URL`
    and friends exist by name once construction succeeds.
    """

    def __init__(self, env=None):
        env = os.environ if env is None else env

        missing = [
            name for name in REQUIRED_VARS if not (env.get(name) or "").strip()
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): "
                + ", ".join(sorted(missing))
                + ". See .env.example for the full list."
            )

        for name in REQUIRED_VARS:
            setattr(self, name, env[name].strip())
        for name in OPTIONAL_VARS:
            value = (env.get(name) or "").strip()
            setattr(self, name, value or None)

        # Drop a non-public embed URL rather than raising: this var is optional,
        # so the page falls back to its "report pending" placeholder instead of
        # taking the whole site down. The URL embeds a token, so it is never logged.
        if self.POWERBI_EMBED_URL and not _is_publish_to_web_url(
            self.POWERBI_EMBED_URL
        ):
            log.warning(
                "POWERBI_EMBED_URL is not a Publish to web URL and was ignored. "
                "Expected https://app.powerbi.com/view?r=<token>; a reportEmbed/"
                "autoAuth URL forces every visitor to sign in. Regenerate it via "
                "File > Embed report > Publish to web (public)."
            )
            self.POWERBI_EMBED_URL = None

    def __repr__(self):
        # Never render secret values.
        return f"<Config SUPABASE_URL={self.SUPABASE_URL!r} secrets=redacted>"

"""Environment configuration with fail-fast validation.

No hardcoded fallbacks for secrets: a missing required variable raises at
startup rather than letting the app run in a half-configured state.
"""

import os

REQUIRED_VARS = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "POWERBI_PUSH_URL",
    "INGEST_TOKEN",
)

OPTIONAL_VARS = ("POWERBI_EMBED_URL",)


class ConfigError(RuntimeError):
    """Raised when the environment is missing something the app requires."""


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

    def __repr__(self):
        # Never render secret values.
        return f"<Config SUPABASE_URL={self.SUPABASE_URL!r} secrets=redacted>"

"""Boot-time configuration.

One frozen dataclass, built once by :meth:`Settings.from_env`, passed explicitly
to everything that needs it. No module in this package reads ``os.environ``
after boot.

The design rule here is *fail closed, at boot, with every problem at once*. A
service that starts with a bad configuration and discovers it on the first
request has turned a deployment error into a production incident, and one that
reports its misconfiguration one item per restart wastes an afternoon.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

__all__ = ["ConfigError", "Settings", "MIN_SECRET_LEN"]

# 32 hex characters is 128 bits of key material. Shorter secrets are brute
# forceable offline once an attacker holds one signed token, and HS256 gives
# them exactly that.
MIN_SECRET_LEN = 32

MAX_TOKEN_TTL = 86400
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LOG_FORMATS = ("json", "text")
_ENVS = ("dev", "staging", "prod")


class ConfigError(Exception):
    """Raised at boot with the complete list of configuration problems."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        joined = "".join("\n  - " + p for p in self.problems)
        super().__init__("invalid configuration:" + joined)


def _int(env: Mapping[str, str], key: str, default: int, problems: List[str],
         minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        problems.append(key + " must be an integer, got " + repr(raw))
        return default
    if minimum is not None and value < minimum:
        problems.append(key + " must be >= " + str(minimum) + ", got " + str(value))
    if maximum is not None and value > maximum:
        problems.append(key + " must be <= " + str(maximum) + ", got " + str(value))
    return value


def _float(env: Mapping[str, str], key: str, default: float, problems: List[str],
           minimum: Optional[float] = None) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        problems.append(key + " must be a number, got " + repr(raw))
        return default
    if minimum is not None and value < minimum:
        problems.append(key + " must be >= " + str(minimum) + ", got " + str(value))
    return value


def _choice(env: Mapping[str, str], key: str, default: str, allowed: tuple,
            problems: List[str], lower: bool = True) -> str:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    value = raw.lower() if lower else raw.upper()
    if value not in allowed:
        problems.append(key + " must be one of " + ", ".join(allowed) + ", got " + repr(raw))
        return default
    return value


@dataclass(frozen=True)
class Settings:
    """Immutable, validated runtime configuration."""

    jwt_secret: str
    db_path: Path
    artifacts_dir: Path
    models_dir: Path
    host: str
    port: int
    token_ttl: int
    audience: str
    rate_capacity: int
    rate_refill: float
    idempotency_ttl: int
    log_level: str
    log_format: str
    env: str
    worker_interval: int

    @property
    def secret_fingerprint(self) -> str:
        """First 8 hex chars of SHA-256 of the secret.

        Safe to log. This exists so that two replicas disagreeing about which
        secret they loaded can be diagnosed from their boot lines, which is
        otherwise a genuinely painful thing to work out.
        """
        digest = hashlib.sha256(self.jwt_secret.encode("utf-8")).hexdigest()
        return digest[:8]

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    def redacted(self) -> Dict[str, Any]:
        """Config as a dict with the secret replaced by its fingerprint."""
        out: Dict[str, Any] = {}
        for key, value in asdict(self).items():
            if key == "jwt_secret":
                out[key] = "sha256:" + self.secret_fingerprint
            elif isinstance(value, Path):
                out[key] = str(value)
            else:
                out[key] = value
        return out

    def ensure_dirs(self) -> None:
        """Create the directories the service writes to.

        Called explicitly rather than from ``from_env`` so that configuration
        parsing stays a pure function and can be unit tested without touching
        the filesystem.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if environ is None else environ
        problems: List[str] = []

        secret = env.get("SENTINELAI_JWT_SECRET", "")
        if not secret:
            problems.append(
                "SENTINELAI_JWT_SECRET is required and has no default. Generate one with: "
                "python3 -c 'import secrets; print(secrets.token_hex(32))'"
            )
        elif len(secret) < MIN_SECRET_LEN:
            problems.append(
                "SENTINELAI_JWT_SECRET must be at least "
                + str(MIN_SECRET_LEN)
                + " characters, got "
                + str(len(secret))
            )

        port = _int(env, "SENTINELAI_PORT", 8088, problems, minimum=1, maximum=65535)
        token_ttl = _int(env, "SENTINELAI_TOKEN_TTL", 3600, problems, minimum=60,
                         maximum=MAX_TOKEN_TTL)
        rate_capacity = _int(env, "SENTINELAI_RATE_CAPACITY", 30, problems, minimum=1)
        rate_refill = _float(env, "SENTINELAI_RATE_REFILL", 0.5, problems, minimum=0.0)
        idem_ttl = _int(env, "SENTINELAI_IDEMPOTENCY_TTL", 86400, problems, minimum=60)
        worker_interval = _int(env, "SENTINELAI_WORKER_INTERVAL", 60, problems, minimum=5)

        log_level = _choice(env, "SENTINELAI_LOG_LEVEL", "INFO", _LOG_LEVELS, problems,
                            lower=False)
        log_format = _choice(env, "SENTINELAI_LOG_FORMAT", "json", _LOG_FORMATS, problems)
        app_env = _choice(env, "SENTINELAI_ENV", "dev", _ENVS, problems)

        host = env.get("SENTINELAI_HOST", "127.0.0.1") or "127.0.0.1"
        audience = env.get("SENTINELAI_AUDIENCE", "sentinelai") or "sentinelai"

        # Binding a security service to every interface is a deliberate act, so
        # it must be a deliberate act in production rather than a leftover.
        if app_env == "prod" and host == "0.0.0.0" and env.get("SENTINELAI_ALLOW_PUBLIC_BIND") != "1":
            problems.append(
                "refusing to bind 0.0.0.0 in prod without SENTINELAI_ALLOW_PUBLIC_BIND=1"
            )

        settings = cls(
            jwt_secret=secret,
            db_path=Path(env.get("SENTINELAI_DB", "data/sentinelai.db")),
            artifacts_dir=Path(env.get("SENTINELAI_ARTIFACTS", "artifacts")),
            models_dir=Path(env.get("SENTINELAI_MODELS", "models")),
            host=host,
            port=port,
            token_ttl=token_ttl,
            audience=audience,
            rate_capacity=rate_capacity,
            rate_refill=rate_refill,
            idempotency_ttl=idem_ttl,
            log_level=log_level,
            log_format=log_format,
            env=app_env,
            worker_interval=worker_interval,
        )

        if problems:
            raise ConfigError(problems)
        return settings

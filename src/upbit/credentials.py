"""Encrypted at-rest storage for the Upbit API key pair.

The keys are typed by the user into the dashboard (never committed, never in
`.env` unless the user puts them there themselves). They are sealed with a
Fernet key that lives beside them in `data_store/`, chmod 0600:

    data_store/.upbit_master.key    <- 32-byte urlsafe key, generated once
    data_store/upbit_credentials.enc <- Fernet token over the JSON payload

If ``UPBIT_KEY_PASSPHRASE`` is set, the master key is derived from it with
PBKDF2 instead of being stored, so the secret never touches the disk.

`data_store/` is gitignored; nothing here may ever be logged or returned by the
API in plaintext — :func:`status` returns masked values only.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from common.config import PROJECT_ROOT, get_env, get_setting
from common.logging import get_logger

log = get_logger(__name__)

_PBKDF2_SALT = b"mais-upbit-credentials-v1"
_PBKDF2_ROUNDS = 390_000


class CredentialsError(RuntimeError):
    """Raised when credentials cannot be sealed, opened, or are absent."""


@dataclass(frozen=True)
class UpbitCredentials:
    access_key: str
    secret_key: str

    def masked(self) -> dict[str, str]:
        return {
            "access_key": _mask(self.access_key),
            "secret_key": _mask(self.secret_key),
        }


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _resolve(path_setting: str, default: str) -> Path:
    raw = Path(get_setting(path_setting, default))
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


def _credentials_path() -> Path:
    return _resolve("upbit.storage.credentials", "data_store/upbit_credentials.enc")


def _master_key_path() -> Path:
    return _credentials_path().with_name(".upbit_master.key")


def _chmod_600(path: Path) -> None:
    with contextlib.suppress(OSError):  # non-POSIX filesystems
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise CredentialsError(
            "cryptography 패키지가 필요합니다. `pip install -e \".[upbit]\"` 로 설치하세요."
        ) from exc

    passphrase = os.environ.get("UPBIT_KEY_PASSPHRASE", "")
    if passphrase:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_PBKDF2_SALT,
            iterations=_PBKDF2_ROUNDS,
        )
        return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8"))))

    key_path = _master_key_path()
    if key_path.exists():
        return Fernet(key_path.read_bytes().strip())

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    _chmod_600(key_path)
    log.info("upbit.credentials.master_key_created", path=str(key_path))
    return Fernet(key)


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def save(access_key: str, secret_key: str) -> None:
    """Seal a key pair to disk, replacing whatever was there before."""
    access_key = (access_key or "").strip()
    secret_key = (secret_key or "").strip()
    if not access_key or not secret_key:
        raise CredentialsError("access_key 와 secret_key 를 모두 입력해야 합니다.")

    payload = json.dumps({"access_key": access_key, "secret_key": secret_key}).encode("utf-8")
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet().encrypt(payload))
    _chmod_600(path)
    log.info("upbit.credentials.saved", path=str(path))


def load() -> UpbitCredentials | None:
    """Return the stored pair, falling back to env vars, or ``None``."""
    path = _credentials_path()
    if path.exists():
        try:
            data = json.loads(_fernet().decrypt(path.read_bytes()).decode("utf-8"))
            return UpbitCredentials(data["access_key"], data["secret_key"])
        except CredentialsError:
            raise
        except Exception as exc:  # corrupt or rotated key material
            log.error("upbit.credentials.unreadable", error=str(exc))
            raise CredentialsError(
                "저장된 API 키를 복호화하지 못했습니다. 대시보드에서 다시 입력해 주세요."
            ) from exc

    env = get_env()
    access = getattr(env, "upbit_access_key", "") or os.environ.get("UPBIT_ACCESS_KEY", "")
    secret = getattr(env, "upbit_secret_key", "") or os.environ.get("UPBIT_SECRET_KEY", "")
    if access and secret:
        return UpbitCredentials(access, secret)
    return None


def require() -> UpbitCredentials:
    creds = load()
    if creds is None:
        raise CredentialsError("업비트 API 키가 등록되지 않았습니다. 설정 탭에서 입력하세요.")
    return creds


def delete() -> bool:
    """Remove the sealed credentials. Returns whether a file was deleted."""
    path = _credentials_path()
    if path.exists():
        path.unlink()
        log.info("upbit.credentials.deleted")
        return True
    return False


def status() -> dict[str, object]:
    """Masked view for the dashboard — never exposes raw key material."""
    stored = _credentials_path().exists()
    try:
        creds = load()
    except CredentialsError as exc:
        return {"configured": False, "stored_on_disk": stored, "error": str(exc)}
    if creds is None:
        return {"configured": False, "stored_on_disk": stored}
    return {
        "configured": True,
        "stored_on_disk": stored,
        "source": "file" if stored else "env",
        **creds.masked(),
    }

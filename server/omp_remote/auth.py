from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import secrets

from .db import Database


class AuthenticationError(Exception):
    """Raised when a credential is absent, malformed, unknown, or revoked."""


@dataclass(frozen=True, slots=True)
class Identity:
    device_id: str
    owner_user: str
    label: str


@dataclass(frozen=True, slots=True)
class EmergencyIdentity:
    credential_id: str
    label: str


class CredentialStore:
    """Issues opaque one-time-display credentials and stores only their hashes."""

    def __init__(self, database: Database, allowed_users: tuple[str, ...]):
        self.database = database
        self.allowed_users = frozenset(allowed_users)

    def issue_device(self, *, owner_user: str, label: str = "") -> tuple[str, str]:
        if owner_user not in self.allowed_users:
            raise ValueError("owner_user is not allowlisted")
        token = _new_token()
        device_id = self.database.insert_device(
            owner_user=owner_user,
            token_hash=hash_token(token),
            label=_clean_label(label),
        )
        return device_id, token

    def authenticate_device(self, authorization: str | None) -> Identity:
        token = parse_bearer(authorization)
        row = self.database.find_active_device(hash_token(token))
        if row is None:
            raise AuthenticationError("invalid credentials")
        return Identity(
            device_id=str(row["id"]),
            owner_user=str(row["owner_user"]),
            label=str(row["label"]),
        )

    def revoke_device(self, device_id: str) -> bool:
        return self.database.revoke_device(device_id)

    def issue_emergency(self, *, label: str = "") -> tuple[str, str]:
        token = _new_token()
        credential_id = self.database.insert_emergency_credential(
            token_hash=hash_token(token), label=_clean_label(label)
        )
        return credential_id, token

    def authenticate_emergency(self, authorization: str | None) -> EmergencyIdentity:
        token = parse_bearer(authorization)
        row = self.database.find_active_emergency(hash_token(token))
        if row is None:
            raise AuthenticationError("invalid emergency credentials")
        return EmergencyIdentity(credential_id=str(row["id"]), label=str(row["label"]))

    def revoke_emergency(self, credential_id: str) -> bool:
        return self.database.revoke_emergency(credential_id)


def _new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def parse_bearer(authorization: str | None) -> str:
    if not isinstance(authorization, str):
        raise AuthenticationError("missing credentials")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token or " " in token:
        raise AuthenticationError("invalid authorization header")
    if len(token) > 512:
        raise AuthenticationError("invalid authorization header")
    try:
        token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AuthenticationError("invalid authorization header") from exc
    return token


def _clean_label(label: str) -> str:
    if not isinstance(label, str):
        raise ValueError("label must be a string")
    label = label.strip()
    if len(label.encode("utf-8")) > 200:
        raise ValueError("label is too long")
    return label

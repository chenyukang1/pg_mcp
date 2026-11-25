"""比对独立配置的预期身份，而不是从可能配错的 DSN 推算身份。"""

import os
from dataclasses import dataclass

from .errors import Issue, ToolFailure

IDENTITY_SQL = (
    "SELECT pg_catalog.current_database(), current_user, "
    "pg_catalog.current_setting('transaction_read_only')"
)


class IdentityMismatch(ToolFailure):
    def __init__(self) -> None:
        super().__init__(Issue.IDENTITY_MISMATCH)


@dataclass(frozen=True)
class ExpectedIdentity:
    database: str
    user: str

    @classmethod
    def from_env(cls, prefix: str, *, required: bool = False) -> ExpectedIdentity | None:
        database = os.getenv(f"{prefix}_EXPECTED_DATABASE")
        user = os.getenv(f"{prefix}_EXPECTED_USER")
        if not (required or database or user):
            return None
        if not (database and user):
            raise ValueError(f"请同时配置 {prefix}_EXPECTED_DATABASE 和 {prefix}_EXPECTED_USER")
        return cls(database, user)

    def verify(self, actual: tuple[str, str, str]) -> None:
        if actual != (self.database, self.user, "on"):
            raise IdentityMismatch()

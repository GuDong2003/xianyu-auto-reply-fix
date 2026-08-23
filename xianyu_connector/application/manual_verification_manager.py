"""Compatibility entry point for the manual browser verification use case."""

from xianyu_connector.application.verification_session_lifecycle import (
    AccountRecoveryPort,
    InvalidVerificationToken,
    RuntimeVerificationPort,
    VerificationSessionNotFound,
)
from xianyu_connector.application.verification_session_manager import (
    VerificationSessionManager,
    VerificationSessionResult,
)

__all__ = [
    "AccountRecoveryPort",
    "InvalidVerificationToken",
    "RuntimeVerificationPort",
    "VerificationSessionManager",
    "VerificationSessionNotFound",
    "VerificationSessionResult",
]

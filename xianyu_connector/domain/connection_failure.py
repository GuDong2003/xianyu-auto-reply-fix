from __future__ import annotations


# These signals bypass broad ``except Exception`` handlers in the legacy connector.
class ManualVerificationRequired(BaseException):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class ConnectorNetworkFailure(BaseException):
    pass


def is_network_failure(status: str | None, message: str | None) -> bool:
    combined = f"{status or ''} {message or ''}".lower()
    return any(
        marker in combined
        for marker in (
            "network",
            "timeout",
            "timed out",
            "connection",
            "cannot connect",
            "dns",
            "temporary failure",
        )
    )

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConnectorSettings:
    database_path: Path
    profiles_root: Path
    master_key_path: Path
    host: str = "127.0.0.1"
    port: int = 8091
    shadow_mode: bool = True
    internal_api_token: str = ""
    require_fixed_egress: bool = False
    expected_egress_ip: str = ""
    egress_check_url: str = "https://api.ipify.org"
    local_verification_handoff_enabled: bool = False
    remote_verification_enabled: bool = False

    @classmethod
    def from_environment(cls) -> ConnectorSettings:
        return cls(
            database_path=Path(os.getenv("DB_PATH", "data/xianyu_data.db")),
            profiles_root=Path(os.getenv("XIANYU_PROFILES_ROOT", "/var/lib/xianyu/accounts")),
            master_key_path=Path(
                os.getenv("XIANYU_MASTER_KEY_PATH", "/run/secrets/xianyu_master_key")
            ),
            host=os.getenv("CONNECTOR_HOST", "127.0.0.1"),
            port=int(os.getenv("CONNECTOR_PORT", "8091")),
            shadow_mode=os.getenv("XIANYU_SHADOW_MODE", "true").lower() == "true",
            internal_api_token=os.getenv("XIANYU_CONNECTOR_INTERNAL_TOKEN", ""),
            require_fixed_egress=os.getenv("XIANYU_REQUIRE_FIXED_EGRESS", "false").lower()
            == "true",
            expected_egress_ip=os.getenv("XIANYU_EXPECTED_EGRESS_IP", ""),
            egress_check_url=os.getenv("XIANYU_EGRESS_CHECK_URL", "https://api.ipify.org"),
            local_verification_handoff_enabled=(
                os.getenv("XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED", "false").lower()
                == "true"
            ),
            remote_verification_enabled=(
                os.getenv("XIANYU_REMOTE_VERIFICATION_ENABLED", "false").lower() == "true"
            ),
        )

    def validate(self) -> None:
        if not self.database_path.exists():
            raise FileNotFoundError(self.database_path)
        if not self.master_key_path.exists():
            raise FileNotFoundError(self.master_key_path)
        if len(self.internal_api_token) < 32:
            raise ValueError("XIANYU_CONNECTOR_INTERNAL_TOKEN must be at least 32 characters")
        if self.require_fixed_egress and not self.expected_egress_ip:
            raise ValueError("XIANYU_EXPECTED_EGRESS_IP is required in fixed egress mode")
        self.profiles_root.mkdir(parents=True, exist_ok=True)

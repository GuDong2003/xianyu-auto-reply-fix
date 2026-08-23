from __future__ import annotations

import uvicorn

from xianyu_connector.api import create_connector_app
from xianyu_connector.settings import ConnectorSettings


def main() -> None:
    settings = ConnectorSettings.from_environment()
    uvicorn.run(
        create_connector_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

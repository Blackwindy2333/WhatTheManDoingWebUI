"""Run the WebUI server: python -m server"""

from __future__ import annotations

import uvicorn

from server.config import DEFAULT_CONFIG_PATH, ensure_config
from server.main import create_app


def main() -> None:
    config = ensure_config(DEFAULT_CONFIG_PATH)
    app = create_app(config, start_aggregator=True)
    ssl_kwargs = {}
    if config.serve.mode == "https" and config.serve.ssl_certfile and config.serve.ssl_keyfile:
        ssl_kwargs = {
            "ssl_certfile": config.serve.ssl_certfile,
            "ssl_keyfile": config.serve.ssl_keyfile,
        }
    uvicorn.run(
        app,
        host=config.serve.host,
        port=config.serve.port,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()

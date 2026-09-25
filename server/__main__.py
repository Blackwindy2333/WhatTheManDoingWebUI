"""Run the WebUI server: python -m server"""

from __future__ import annotations

import uvicorn

from server.config import DEFAULT_CONFIG_PATH, ensure_config
from server.log_setup import get_logger, setup_logging
from server.main import create_app


def main() -> None:
    config = ensure_config(DEFAULT_CONFIG_PATH)
    setup_logging(config.log)
    log = get_logger("startup")
    log.info(
        "starting webui host=%s port=%s mode=%s trust_proxy=%s",
        config.serve.host,
        config.serve.port,
        config.serve.mode,
        config.trust_proxy,
    )
    if config.serve.mode == "http":
        log.info(
            "listening plain HTTP — if you use an HTTPS tunnel/domain, "
            "terminate TLS on the tunnel/reverse-proxy and forward HTTP here; "
            "do not point HTTPS clients at this port"
        )
    app = create_app(config, start_aggregator=True)
    ssl_kwargs = {}
    if config.serve.mode == "https" and config.serve.ssl_certfile and config.serve.ssl_keyfile:
        ssl_kwargs = {
            "ssl_certfile": config.serve.ssl_certfile,
            "ssl_keyfile": config.serve.ssl_keyfile,
        }
        log.info("TLS enabled cert=%s", config.serve.ssl_certfile)
    try:
        uvicorn.run(
            app,
            host=config.serve.host,
            port=config.serve.port,
            proxy_headers=config.trust_proxy,
            forwarded_allow_ips=config.forwarded_allow_ips,
            **ssl_kwargs,
        )
    finally:
        log.info("webui stopped")


if __name__ == "__main__":
    main()

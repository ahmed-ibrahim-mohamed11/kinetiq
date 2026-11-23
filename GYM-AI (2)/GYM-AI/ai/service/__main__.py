"""python -m ai.service [--host 127.0.0.1] [--port 8000] [--device cpu|cuda]"""
import argparse
import logging
import sys

import uvicorn

from ai.service.app import Settings, create_app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GYM-AI local analysis service (no authentication)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--device", choices=["cpu", "cuda"], default=None,
                    help="default: $GYM_AI_DEVICE or cpu")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("gym_ai.service").warning(
            "binding to %s: this service has NO authentication — do not expose it beyond this machine",
            args.host)
    override = {"device": args.device} if args.device else {}
    uvicorn.run(create_app(Settings.from_env(**override)), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

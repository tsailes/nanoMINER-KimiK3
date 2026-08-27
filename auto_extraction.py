"""Compatibility entry point for the corrected Kimi K3 batch command."""

from __future__ import annotations

import sys

from nanominer_k3.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["batch", *sys.argv[1:]]))

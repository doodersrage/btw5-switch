#!/usr/bin/env python3
"""Run btw5-switch straight from a source checkout (installed copies use the 'btw5-switch' entry point)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from btw5.cli import main  # noqa: E402

main()

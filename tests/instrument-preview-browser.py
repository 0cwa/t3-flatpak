#!/usr/bin/env python3
"""Expose shipped functions only in a disposable runtime-test archive."""

import argparse
from pathlib import Path

from runtime_module_bindings import instrument_archive


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("archive", type=Path)
parser.add_argument("output_directory", type=Path)
parser.add_argument("--archive-base", default="/app/bin/t3code/resources/app.asar")
args = parser.parse_args()
instrument_archive(args.archive, args.output_directory, args.archive_base)

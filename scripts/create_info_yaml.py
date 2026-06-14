#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import PODCAST_INFO_FILENAME, load_config, scan_podcast_paths


def info_yaml_stub(show_name: str) -> str:
    return f"""show: {show_name}
station: TBD

tags:
  - To Be Cataloged

notes: |
  Free text.
"""


def create_missing_info_files(config_path: str | Path, dry_run: bool = False) -> list[str]:
    config = load_config(Path(config_path).resolve())
    results: list[str] = []

    for podcast_path in scan_podcast_paths(config):
        info_path = podcast_path / PODCAST_INFO_FILENAME
        if info_path.exists():
            results.append(f"SKIP exists: {info_path}")
            continue

        results.append(f"{'DRY' if dry_run else 'CREATE'} {info_path}")
        if not dry_run:
            info_path.write_text(info_yaml_stub(podcast_path.name), encoding="utf-8")

    if not results:
        results.append("No podcast folders found.")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create missing _info.yaml files for podcast folders."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to TOML config file. Defaults to config.toml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing files.",
    )
    args = parser.parse_args()

    for line in create_missing_info_files(args.config, dry_run=args.dry_run):
        print(line)


if __name__ == "__main__":
    main()

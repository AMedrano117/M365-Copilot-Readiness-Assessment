"""Write App Builder files from an existing dashboard JSON package, offline and without tenant access.

Usage:
    python tools/build_app_builder_export.py "<report-build>/JSON/index.json" [--out FOLDER]

The default folder is <report-build>/App Builder beside the reports.
Older json/<assessment-stem>/index.json packages retain their original default folder.
The folder must be new or empty; existing files are never overwritten.
"""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Core.app_builder_export import write_app_builder_export  # noqa: E402
from Core.dashboard_package import read_dashboard_package  # noqa: E402
from Core.finding_evidence import build_finding_evidence  # noqa: E402
from Core.technical_guidance import attach_technical_guidance  # noqa: E402
from Core.export_paths import APP_BUILDER_FOLDER, JSON_FOLDER  # noqa: E402


def default_app_builder_folder(index):
    index = Path(index)
    current = re.fullmatch(re.escape(JSON_FOLDER) + r'( \([1-9][0-9]*\))?', index.parent.name, re.I)
    if current:
        return index.parent.parent / (APP_BUILDER_FOLDER + (current.group(1) or ''))
    return index.parent.parent.parent / f'{index.parent.name}_app_builder'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('index', type=Path, help='Path to the dashboard package index.json')
    parser.add_argument('--out', type=Path, help='Destination folder (new or empty)')
    args = parser.parse_args(argv)
    index = args.index.resolve()
    payload = attach_technical_guidance(read_dashboard_package(index))
    folder = args.out or default_app_builder_folder(index)
    result = write_app_builder_export(build_finding_evidence(payload), folder,
                                      deliverables=payload.get('deliverables') or {})
    print(f"App Builder files: {len(result['files'])} written to {result['folder']}")
    print(f"Upload guide: {result['guide']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Verify that every registered input has its original SHA256 (standard library)."""
import csv
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    with (ROOT / 'data/MANIFEST.csv').open(encoding='utf-8-sig', newline='') as stream:
        entries = list(csv.DictReader(stream))
    if not entries:
        errors.append('input manifest is empty')
    for entry in entries:
        path = (ROOT / entry['path_or_uri']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            errors.append(f"missing or unsafe input: {entry['path_or_uri']}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry['sha256']:
            errors.append(f"SHA256 mismatch: {entry['path_or_uri']}")
    for error in errors:
        print(error)
    print(f"Input hashes: {'FAIL' if errors else 'PASS'} ({len(entries)} files)")
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())

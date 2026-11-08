"""Report requirements.txt entries the venv doesn't satisfy.

setup_service.sh runs this at every boot to decide whether pip is needed, so
an up-to-date device starts COD without touching the network or remounting
the read-only rootfs. Exit 0 when everything is satisfied, 1 otherwise (the
unmet entries go to stdout for the log).
"""

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement

DEFAULT_PATH = Path(__file__).resolve().parent / "requirements.txt"


def parse(path):
    reqs = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            reqs.append(Requirement(line))
    return reqs


def unmet(path):
    missing = []
    for req in parse(path):
        try:
            have = version(req.name)
        except PackageNotFoundError:
            missing.append(f"{req} (missing)")
            continue
        if not req.specifier.contains(have, prereleases=True):
            missing.append(f"{req} (have {have})")
    return missing


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    missing = unmet(argv[0] if argv else DEFAULT_PATH)
    for entry in missing:
        print(entry)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())

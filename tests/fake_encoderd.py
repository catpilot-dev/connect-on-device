#!/usr/bin/env python3
"""Stand-in for `encoderd --clip` in tests: same argv shape, exit codes and SIGTERM deafness.

clips.encode_one runs this exactly as it runs the real binary, so it mirrors the
behaviour the worker has to cope with:
  - fewer than five arguments after the program name: usage line on stderr, exit 2
    (what the support probe looks for);
  - a malformed option or missing input: "clip encoding failed: ..." on stderr, exit 1;
  - the output file is opened at start and filled later (the real encoder writes
    progressively), and OUTPUT.lock exists for the whole run (VideoWriter's lock);
  - SIGTERM is ignored (the real clip path never reads its exit flag), so only
    SIGKILL stops it.

Environment knobs:
  FAKE_ENCODERD_SLEEP       seconds to "encode" before writing the output (default 0.2)
  FAKE_ENCODERD_BYTES       bytes written to OUTPUT (default 4096)
  FAKE_ENCODERD_EXIT        exit code (default 0)
  FAKE_ENCODERD_LEAVE_LOCK  "1" leaves OUTPUT.lock behind, like a writer that never ran its destructor
"""
import json
import os
import signal
import sys
import time

USAGE = ("usage: encoderd --clip OUTPUT START DURATION [--bitrate BPS] [--speedup N] "
         "[--metadata JSON] SEGMENT [SEGMENT ...]")


def parse(argv: list[str]) -> tuple[str, list[str]]:
    """(output, inputs) from encoderd's --clip argv; ValueError on anything it would refuse."""
    output = argv[2]
    float(argv[3]), float(argv[4])
    i = 5
    while i < len(argv) and argv[i].startswith("--"):
        option = argv[i]
        i += 1
        if option == "--":
            break
        if i == len(argv):
            raise ValueError("missing clip option value")
        if option in ("--bitrate", "--speedup"):
            int(argv[i])
        elif option == "--metadata":
            json.loads(argv[i])
        else:
            raise ValueError(f"unknown clip option: {option}")
        i += 1
    inputs = argv[i:]
    if not inputs:
        raise ValueError("missing clip input")
    for path in inputs:
        if not os.path.isfile(path):
            raise ValueError(f"missing input: {path}")
    return output, inputs


def main(argv: list[str]) -> int:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if len(argv) < 6 or argv[1] != "--clip":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        output, _ = parse(argv)
    except ValueError as e:
        print(f"clip encoding failed: {e}", file=sys.stderr)
        return 1
    lock = output + ".lock"
    open(lock, "w").close()
    with open(output, "wb") as f:
        time.sleep(float(os.environ.get("FAKE_ENCODERD_SLEEP", "0.2")))
        f.write(b"\x00" * int(os.environ.get("FAKE_ENCODERD_BYTES", "4096")))
    if os.environ.get("FAKE_ENCODERD_LEAVE_LOCK") != "1":
        os.unlink(lock)
    return int(os.environ.get("FAKE_ENCODERD_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main(sys.argv))

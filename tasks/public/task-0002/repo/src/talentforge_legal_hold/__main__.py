from talentforge_legal_hold.cli import _entrypoint

# SystemExit, not a bare call: the return value IS the process exit code, and an
# export that could not complete must not look like a clean run to the scheduler.
if __name__ == "__main__":
    raise SystemExit(_entrypoint())

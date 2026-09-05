from ironvale_topup.cli import _entrypoint

# SystemExit, not a bare call: the return value IS the process exit code, and a
# top-up that could not finish must not look clean to the scheduler.
if __name__ == "__main__":
    raise SystemExit(_entrypoint())

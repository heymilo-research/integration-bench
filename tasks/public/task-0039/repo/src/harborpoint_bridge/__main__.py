from harborpoint_bridge.cli import _entrypoint

# SystemExit, not a bare call: the return value IS the process exit code, and a
# payroll bridge that could not place its punches must not look clean to the
# scheduler that runs it.
if __name__ == "__main__":
    raise SystemExit(_entrypoint())

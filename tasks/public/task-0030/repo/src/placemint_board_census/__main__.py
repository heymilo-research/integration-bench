from placemint_board_census.cli import _entrypoint

# SystemExit, not a bare call: the return value IS the process exit code, and a
# run that could not complete must not look clean to the scheduler.
if __name__ == "__main__":
    raise SystemExit(_entrypoint())

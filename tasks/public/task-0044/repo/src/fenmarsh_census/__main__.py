from fenmarsh_census.cli import _entrypoint

# SystemExit, not a bare call: the return value IS the process exit code, and a
# census that could not be taken must not look clean to the scheduler.
if __name__ == "__main__":
    raise SystemExit(_entrypoint())

from sandhurst_rollup.cli import _entrypoint

# SystemExit, not a bare call: the return value IS the process exit code, and a
# rollup that could not be produced must not look like a clean run to the
# scheduler that feeds Finance's loader.
if __name__ == "__main__":
    raise SystemExit(_entrypoint())

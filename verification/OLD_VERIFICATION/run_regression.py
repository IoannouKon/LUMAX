#!/usr/bin/env python3
"""Run the 180-case LUMAX regression: start | status | stop | check."""
import sys

sys.dont_write_bytecode = True
from run_test_cases import main

if __name__ == '__main__':
    try:
        raise SystemExit(main(suite='regression'))
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1)

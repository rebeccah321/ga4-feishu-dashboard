#!/usr/bin/env python3
import sys

from ga4_pipeline import main

if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1].startswith("-"):
        sys.argv.insert(1, "setup-base")
    main()

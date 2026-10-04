"""`python -m warden` — the same entry point as the `warden` console script.

Here so a caller can invoke the CLI through THIS interpreter rather than
through whatever `warden` happens to be first on PATH. `warden ship`'s gate
parity step needs exactly that: it must run the command CI runs, in the
environment it is already running in, and a console script resolved off PATH is
a different (possibly stale, possibly absent) install.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

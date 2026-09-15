import os

from hypothesis import settings

# CI runs a fixed set of examples so failures reproduce; local runs explore randomly.
settings.register_profile("ci", derandomize=True, database=None, deadline=None, print_blob=True)
settings.register_profile("dev", deadline=None)
settings.load_profile("ci" if os.environ.get("CI") else "dev")

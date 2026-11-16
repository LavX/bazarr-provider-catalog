"""Stand-in for bazarr/provider_hub/registry.py.

It holds the registered provider hub ids that the vendored manifest module reads.
The 2.6.2 snapshot does not import this module; it is kept so that the three
snapshot directories share one layout.
"""
_REGISTERED_PROVIDER_HUB_IDS: set[str] = set()

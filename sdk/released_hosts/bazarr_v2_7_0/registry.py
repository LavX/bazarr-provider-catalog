# Stand-in for bazarr/provider_hub/registry.py: the catalog check runs with no
# hub plugins registered, so the built-in set is the host's own providers.
_REGISTERED_PROVIDER_HUB_IDS: set[str] = set()

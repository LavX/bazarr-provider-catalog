"""Fixture translator plugin used by the catalog tooling tests.

It exists so the plugins feed builder has a real schema 2 entry to validate and sign.
It is not a working translator and no host can install it.
"""


class FixtureTranslator:
    def translate(self, lines, source_language, target_language):
        return list(lines)

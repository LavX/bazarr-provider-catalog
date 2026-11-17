"""Fixture translator plugin used by the catalog tooling tests.

It exists so the plugins feed builder has a real schema 2 entry to validate and sign.
It is not a working translator and lives under tests/fixtures so it is never published.
"""


class FixtureTranslator:
    def translate(self, lines, source_language, target_language):
        return list(lines)

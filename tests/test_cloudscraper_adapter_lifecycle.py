"""Adapter ownership across TLS rotations in every cloudscraper-backed provider."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[1]
FACTORIES = {
    "legendasdivx": "_create_cloudscraper",
    "napiprojekt": "_create_cloudscraper",
    "opensubtitles_org": "_create_cloudscraper_session",
    "sub_scene": "_create_cloudscraper_session",
    "subs4series": "_create_cloudscraper",
    "turkcealtyaziorg": "_create_cloudscraper_session",
    "wizdom": "_create_cloudscraper_session",
    "yavkanet": "_create_cloudscraper_session",
}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


def load_provider(provider_id):
    name = f"adapter_lifecycle_{provider_id}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "providers" / provider_id / "provider.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BeforeNetwork(RuntimeError):
    pass


class TrackingAdapter(requests.adapters.HTTPAdapter):
    def __init__(self):
        self.closes = 0
        super().__init__()

    def close(self):
        self.closes += 1
        super().close()


class CloudscraperAdapterLifecycleTests(unittest.TestCase):
    def test_two_real_fingerprint_rotations_close_each_replaced_adapter(self):
        for provider_id, factory_name in FACTORIES.items():
            with self.subTest(provider=provider_id):
                module = load_provider(provider_id)
                session = getattr(module, factory_name)(USER_AGENT)
                try:
                    session._apply_request_throttling = lambda: None
                    session.enable_stealth = False
                    session.anti_detection_manager = None
                    session.timing_orchestrator = None
                    for _rotation in range(2):
                        previous = session.get_adapter("https://rotation.invalid/")
                        with patch.object(previous, "close", wraps=previous.close) as closed, patch.object(
                            session, "perform_request", side_effect=BeforeNetwork
                        ):
                            session.tls_fingerprinting_manager.force_rotation()
                            with self.assertRaises(BeforeNetwork):
                                session.get("https://rotation.invalid/", timeout=1)
                            self.assertIsNot(session.get_adapter("https://rotation.invalid/"), previous)
                            closed.assert_called_once_with()
                finally:
                    session.close()

    def test_failed_mount_preserves_the_previous_adapter(self):
        for provider_id, factory_name in FACTORIES.items():
            with self.subTest(provider=provider_id):
                module = load_provider(provider_id)
                session = requests.Session()
                previous = TrackingAdapter()
                session.mount("https://", previous)
                original_mount = session.mount

                def failing_mount(prefix, adapter):
                    if prefix == "https://":
                        raise RuntimeError("mount refused")
                    return original_mount(prefix, adapter)

                session.mount = failing_mount
                replacement = TrackingAdapter()
                try:
                    with patch.object(module.cloudscraper, "create_scraper", return_value=session):
                        wrapped = getattr(module, factory_name)(USER_AGENT)
                    with self.assertRaisesRegex(RuntimeError, "mount refused"):
                        wrapped.mount("https://", replacement)
                    self.assertIs(wrapped.get_adapter("https://rotation.invalid/"), previous)
                    self.assertEqual(previous.closes, 0)
                finally:
                    session.close()
                    replacement.close()

    def test_an_adapter_still_mounted_elsewhere_is_closed_only_after_its_last_replacement(self):
        for provider_id, factory_name in FACTORIES.items():
            with self.subTest(provider=provider_id):
                module = load_provider(provider_id)
                session = requests.Session()
                previous = TrackingAdapter()
                session.mount("https://", previous)
                session.mount("https://specific.invalid/", previous)
                try:
                    with patch.object(module.cloudscraper, "create_scraper", return_value=session):
                        wrapped = getattr(module, factory_name)(USER_AGENT)
                    wrapped.mount("https://", TrackingAdapter())
                    self.assertEqual(previous.closes, 0)
                    wrapped.mount("https://specific.invalid/", TrackingAdapter())
                    self.assertEqual(previous.closes, 1)
                finally:
                    session.close()

    def test_wrapping_once_preserves_active_adapter_close(self):
        for provider_id, factory_name in FACTORIES.items():
            with self.subTest(provider=provider_id):
                module = load_provider(provider_id)
                session = requests.Session()
                with patch.object(module.cloudscraper, "create_scraper", return_value=session):
                    wrapped = getattr(module, factory_name)(USER_AGENT)
                    self.assertIs(getattr(module, factory_name)(USER_AGENT), wrapped)
                first = TrackingAdapter()
                last = TrackingAdapter()
                try:
                    wrapped.mount("https://", first)
                    wrapped.mount("https://", first)
                    self.assertEqual(first.closes, 0)
                    wrapped.mount("https://", last)
                    self.assertEqual(first.closes, 1)
                    self.assertEqual(last.closes, 0)
                finally:
                    session.close()
                self.assertEqual(last.closes, 1)

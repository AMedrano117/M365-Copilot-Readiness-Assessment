import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Core.get_purview_client import get_purview_client
from Core.purview_cache_import import load_purview_cache


class PurviewCacheImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "purview-cache.json"
        self.collected = datetime.now(timezone.utc) - timedelta(days=6)
        self.payload = {
            "dlp_policies": {"available": True, "availability_status": "available", "count": 2,
                             "policies": [{"Name": "Policy A", "Mode": "Enforce"},
                                          {"Name": "Policy B", "Mode": "TestWithoutNotifications"}]},
            "dlp_rules": {"available": True, "availability_status": "available", "count": 1,
                          "rules": {"Name": "Rule A", "Disabled": False}},
            "sensitivity_labels": {"available": True, "availability_status": "available", "count": 1,
                                   "labels": [{"Name": "Confidential"}]},
            "information_barriers": {"available": True, "availability_status": "available",
                                     "count": 0, "policies": []},
            "audit_config": {"available": False, "availability_status": "unavailable", "data": {},
                             "reason": "Original collection denied this source."},
        }

    def write_cache(self, **overrides):
        document = {
            "schema_version": 2,
            "tenant_id": "11111111-2222-3333-4444-555555555555",
            "cached_at_epoch": self.collected.timestamp(),
            "purview_data_json": json.dumps(self.payload),
        }
        document.update(overrides)
        self.path.write_text(json.dumps(document), encoding="utf-8")

    def test_import_is_local_and_keeps_policy_objects_and_collection_date(self):
        self.write_cache()
        original_get = os.environ.get

        def protected_env_get(key, default=None):
            if key.startswith("PURVIEW_") or key in {"TENANT_ID", "CLIENT_SECRET", "CLIENT_ID"}:
                raise AssertionError("Offline import read collector environment")
            return original_get(key, default)

        with ExitStack() as blocked:
            for target in (
                "Core.get_purview_client.load_purview_data_from_stdin",
                "sys.stdin.read", "socket.socket.connect", "socket.create_connection",
                "subprocess.Popen", "builtins.input",
            ):
                blocked.enter_context(patch(target, side_effect=AssertionError("Live operation called")))
            blocked.enter_context(patch.object(os.environ, "get", side_effect=protected_env_get))
            result = load_purview_cache(self.path)

        self.assertEqual(result["collected_at"], self.collected.isoformat())
        self.assertEqual(result["source_file"], self.path.name)
        client = result["service_info"]["_client"]
        self.assertEqual(client.dlp_policies["total_policies"], 2)
        self.assertEqual(client.dlp_policies["enabled_policies"], 2)
        self.assertEqual(client.dlp_policies["policies"][0]["Name"], "Policy A")
        self.assertEqual(client.dlp_rules["total_rules"], 1)
        self.assertEqual(client.sensitivity_labels["total_labels"], 1)
        self.assertTrue(client.information_barriers["available"])
        self.assertEqual(client.information_barriers["total_policies"], 0)
        self.assertFalse(client.audit_config["available"])
        self.assertEqual(client.collection_status["audit_config"]["availability_status"], "unavailable")
        self.assertEqual(client.collection_status["dlp_policies"]["collected_at"], self.collected.isoformat())
        self.assertEqual(client.cache_provenance["freshness"], "Historical cache")
        rec = result["service_info"]["recommendations"][0]
        self.assertEqual(rec["Disposition"], "Reference")
        self.assertEqual(rec["Status"], "Imported")
        self.assertEqual(rec["FindingKey"], "purview.cached_configuration")
        self.assertEqual(rec["EvidenceKey"], "purview_policy_detail")
        self.assertIn("2 DLP policies", rec["Observation"])
        self.assertNotIn("licenses", result["service_info"])

    def test_old_cache_is_stale_without_changing_original_availability(self):
        self.collected = datetime.now(timezone.utc) - timedelta(days=60)
        self.write_cache()
        result = load_purview_cache(self.path)
        client = result["service_info"]["_client"]
        self.assertEqual(client.cache_provenance["freshness"], "Stale")
        self.assertTrue(client.dlp_policies["available"])
        self.assertIn("marked stale", result["service_info"]["recommendations"][0]["Observation"])

    def test_v3_cache_keeps_copilot_fields_and_original_source_date(self):
        self.payload['collected_at'] = self.collected.isoformat()
        self.payload['dlp_policies']['policies'][0]['EnforcementPlanes'] = ['CopilotExperiences']
        self.payload['dlp_rules']['rules']['RestrictAccess'] = [{'setting': 'ExcludeContentProcessing', 'value': 'Block'}]
        self.write_cache(schema_version=3)
        client = load_purview_cache(self.path)['service_info']['_client']
        self.assertEqual(client.collected_at, self.collected.isoformat())
        self.assertEqual(client.dlp_policies['policies'][0]['EnforcementPlanes'], ['CopilotExperiences'])
        self.assertEqual(client.dlp_rules['rules'][0]['RestrictAccess'][0]['value'], 'Block')

    def test_invalid_cache_identity_timestamp_and_schema_rejected(self):
        for overrides in (
            {"schema_version": 1}, {"schema_version": 4}, {"tenant_id": "tenant.example"},
            {"cached_at_epoch": "yesterday"}, {"cached_at_epoch": float("inf")},
            {"cached_at_epoch": -10}, {"cached_at_epoch": True},
            {"cached_at_epoch": (datetime.now(timezone.utc) + timedelta(days=1)).timestamp()},
            {"purview_data_json": {}}, {"purview_data_json": "not-json"},
            {"purview_data_json": "[]"}, {"purview_data_json": '{"unknown": {}}'},
        ):
            with self.subTest(overrides=list(overrides)):
                self.write_cache(**overrides)
                with self.assertRaises(ValueError):
                    load_purview_cache(self.path)

    def test_invalid_policy_shapes_cannot_become_empty_success(self):
        for section in (
            [], {"available": "false", "policies": []},
            {"available": True}, {"available": True, "policies": "not a policy"},
            {"available": True, "policies": [1]},
            {"available": True, "policies": [], "count": -1},
        ):
            self.payload = {"dlp_policies": section}
            self.write_cache()
            with self.subTest(section=section):
                with self.assertRaises(ValueError):
                    load_purview_cache(self.path)

    def test_explicit_hydration_bypasses_global_payload(self):
        with patch("Core.get_purview_client.load_purview_data_from_stdin", side_effect=AssertionError("Read stdin")):
            client = asyncio.run(get_purview_client("tenant", payload=self.payload), debug=False)
        self.assertEqual(client.dlp_policies["total_policies"], 2)
        with self.assertRaises(ValueError):
            asyncio.run(get_purview_client("tenant", payload=[]), debug=False)


if __name__ == "__main__":
    unittest.main()

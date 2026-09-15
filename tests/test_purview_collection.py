import unittest
import json
import os

import Core.get_purview_client as purview_module
from Core.get_purview_client import collection_state, extract_collection, extract_object
from Core.orchestrator_powershell import _set_purview_runtime_payload


class PurviewCollectionContractTests(unittest.TestCase):
    def test_explicit_failure_is_not_interpreted_as_empty_success(self):
        payload = {
            "dlp_rules": {
                "available": False,
                "availability_status": "unavailable",
                "rules": [],
                "reason": "The signed-in user is not authorized.",
                "required_role": "View-Only DLP Compliance Management",
            }
        }

        rows, available = extract_collection(payload, "dlp_rules", "rules")
        state = collection_state(payload, "dlp_rules")

        self.assertEqual(rows, [])
        self.assertFalse(available)
        self.assertEqual(state["availability_status"], "unavailable")
        self.assertIn("not authorized", state["reason"])
        self.assertEqual(state["required_role"], "View-Only DLP Compliance Management")

    def test_successful_zero_record_collection_remains_available(self):
        payload = {
            "dlp_policies": {
                "available": True, "availability_status": "available",
                "count": 0, "policies": [],
            }
        }

        rows, available = extract_collection(payload, "dlp_policies", "policies")
        state = collection_state(payload, "dlp_policies")

        self.assertEqual(rows, [])
        self.assertTrue(available)
        self.assertEqual(state["records_collected"], 0)

    def test_status_aware_singleton_retains_availability(self):
        payload = {
            "audit_config": {
                "available": True,
                "data": {"UnifiedAuditLogIngestionEnabled": True},
            }
        }

        value, available = extract_object(payload, "audit_config")

        self.assertTrue(available)
        self.assertTrue(value["UnifiedAuditLogIngestionEnabled"])

    def test_optional_source_is_labeled_optional(self):
        payload = {
            "ediscovery_cases": {
                "available": False, "availability_status": "unavailable",
                "optional": True, "cases": [], "reason": "Feature not enabled.",
            }
        }

        state = collection_state(payload, "ediscovery_cases", optional=True)

        self.assertTrue(state["optional"])
        self.assertFalse(state["available"])

    def test_large_payload_is_passed_in_memory_not_windows_environment(self):
        prior_source = os.environ.get("PURVIEW_DATA_SOURCE")
        prior_json = os.environ.get("PURVIEW_DATA_JSON")
        prior_cache = purview_module._PURVIEW_DATA_CACHE
        try:
            payload = {"dlp_rules": {"available": True, "rules": [{
                "Name": "x" * 40000,
            }]}}
            parsed = _set_purview_runtime_payload(json.dumps(payload), "subprocess")

            self.assertEqual(parsed, payload)
            self.assertNotIn("PURVIEW_DATA_JSON", os.environ)
            self.assertEqual(purview_module.load_purview_data_from_stdin(), payload)
        finally:
            purview_module._PURVIEW_DATA_CACHE = prior_cache
            if prior_source is None:
                os.environ.pop("PURVIEW_DATA_SOURCE", None)
            else:
                os.environ["PURVIEW_DATA_SOURCE"] = prior_source
            if prior_json is None:
                os.environ.pop("PURVIEW_DATA_JSON", None)
            else:
                os.environ["PURVIEW_DATA_JSON"] = prior_json


if __name__ == "__main__":
    unittest.main()

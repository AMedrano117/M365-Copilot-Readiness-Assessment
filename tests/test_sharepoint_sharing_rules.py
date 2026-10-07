"""SharePoint sharing settings and Data access governance sharing-link reports."""

import tempfile
import unittest
from pathlib import Path

from Core.data_exposure_assessment import _scan_reports, detect_sharepoint_report, sharing_link_type
from Core.portal_report_import import validate_report_tenants
from Core.sharepoint_governance import build_sharepoint_recommendations, setting_label, setting_name

LINK_HEADER = ("Site ID,URL,SiteName,Links created,Primary admin,Primary admin email,Teams attached,Site template,"
               "Site sensitivity label ID,SiteSensitivity,Unmanaged devices,External sharing,Privacy")


def governance(settings):
    return {"available": True, "availability_status": "available",
            "tenant": {"available": True, "settings": settings}, "sites": {"available": False}}


class SharingSettingTests(unittest.TestCase):
    def test_numeric_values_from_sharepoint_powershell_are_named(self):
        self.assertEqual(setting_name("SharingCapability", 2), "externaluserandguestsharing")
        self.assertEqual(setting_name("DefaultSharingLinkType", "3"), "anonymousaccess")
        self.assertEqual(setting_name("SharingCapability", "ExternalUserAndGuestSharing"), "externaluserandguestsharing")
        self.assertEqual(setting_label("OneDriveSharingCapability", 1), "new and existing guests")
        self.assertEqual(setting_name("PreventExternalUsersFromResharing", True), "true")

    def test_anyone_default_from_numeric_settings_is_a_sharing_action(self):
        rows = build_sharepoint_recommendations(governance({
            "SharingCapability": 2, "OneDriveSharingCapability": 1, "DefaultSharingLinkType": 3,
            "RequireAnonymousLinksExpireInDays": 5, "FileAnonymousLinkType": 1, "FolderAnonymousLinkType": 1}))
        finding = next(row for row in rows if row["FindingKey"] == "sharepoint.sharing.anyone_enabled")
        self.assertEqual((finding["Disposition"], finding["ControlId"], finding["Priority"]), ("Action", "CONTENT.SHARING", "Medium"))
        self.assertIn("Anyone links are the default link type and expire after 5 day(s).", finding["Observation"])

    def test_anyone_default_without_expiry_is_high(self):
        rows = build_sharepoint_recommendations(governance({"SharingCapability": 2, "DefaultSharingLinkType": 3}))
        finding = next(row for row in rows if row["FindingKey"] == "sharepoint.sharing.permissive_anonymous_defaults")
        self.assertEqual((finding["Disposition"], finding["Priority"]), ("Action", "High"))
        self.assertIn("SharePoint sharing is Anyone links and guests", finding["Observation"])

    def test_organization_default_link_is_a_medium_condition(self):
        rows = build_sharepoint_recommendations(governance({"SharingCapability": 1, "DefaultSharingLinkType": 2}))
        finding = next(row for row in rows if row["FindingKey"] == "sharepoint.sharing.organization_default")
        self.assertEqual((finding["Disposition"], finding["ControlId"], finding["Priority"]), ("Action", "CONTENT.SHARING", "Medium"))

    def test_specific_people_default_has_no_sharing_action(self):
        rows = build_sharepoint_recommendations(governance({"SharingCapability": 0, "DefaultSharingLinkType": 1}))
        self.assertFalse(any(row["FindingKey"].startswith("sharepoint.sharing.") for row in rows))


class SharingLinkReportTests(unittest.TestCase):
    def write(self, folder, name, rows=()):
        path = Path(folder) / name
        path.write_text(LINK_HEADER + "\n" + "".join(row + "\n" for row in rows), encoding="utf-8")
        return path

    def test_header_identifies_the_report_family_and_file_name_the_link_type(self):
        self.assertEqual(detect_sharepoint_report(LINK_HEADER.split(",")), "sharing_links_activity")
        self.assertEqual(sharing_link_type("Anyone_links_2026-09-16.csv"), "Anyone links")
        self.assertEqual(sharing_link_type("People_in_your_organization_links_2026-09-15.csv"), "People in your organization links")
        self.assertEqual(sharing_link_type("Specific_people_links_shared_externally_2026-09-15.csv"), "Specific people links shared externally")
        self.assertEqual(sharing_link_type("renamed.csv"), "Sharing links")

    def test_header_only_reports_are_recognized_as_no_activity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, "Anyone_links_2026-09-16.csv")
            result = _scan_reports([path], "sam", "2026-09-23", None)
            receipt = []
            validate_report_tenants([str(path)], "11111111-1111-1111-1111-111111111111", receipt)
        self.assertFalse(result["errors"])
        self.assertEqual(result["files_recognized"], 1)
        self.assertEqual(receipt[0]["Status"], "no_rows")

    def test_rows_become_attributed_activity_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, "Anyone_links_2026-09-16.csv", [
                "site-1,https://contoso.sharepoint.com/sites/finance,Finance,4,Owner,owner@contoso.com,No,Team,,,,Anyone,Private"])
            result = _scan_reports([path], "sam", "2026-09-23", None)
        observation = next(row for row in result["observations"] if row["source_schema"] == "sharing_links_activity")
        self.assertEqual((observation["label"], observation["value"], observation["unit"]),
                         ("Anyone links created in the report period", 4, "links"))


if __name__ == "__main__":
    unittest.main()

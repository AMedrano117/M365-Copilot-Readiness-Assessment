"""Sharing configuration labels retain property types and never imply assurance."""

import copy
import unittest

from Core.customer_report import _settings, _sharing_setting_value


class CustomerSharingSettingsTests(unittest.TestCase):
    def test_all_sharing_capabilities_support_documented_names_and_numeric_serialization(self):
        values = (
            (0, 'Disabled', 'External sharing disabled'),
            (1, 'ExternalUserSharingOnly', 'New and existing guests; no Anyone links'),
            (2, 'ExternalUserAndGuestSharing', 'Anyone links, new guests, and existing guests'),
            (3, 'ExistingExternalUserSharingOnly', 'Existing guests only'),
        )
        for key in ('SharingCapability', 'OneDriveSharingCapability'):
            for number, name, expected in values:
                for value in (number, str(number), name):
                    with self.subTest(key=key, value=value):
                        self.assertEqual(_sharing_setting_value(key, value), expected)

    def test_default_link_type_uses_its_own_enum(self):
        for number, name, expected in (
            (0, 'None', 'Widest sharing scope allowed by other sharing settings'),
            (1, 'Direct', 'Specific people'),
            (2, 'Internal', 'People in the organization'),
            (3, 'AnonymousAccess', 'Anyone link'),
        ):
            for value in (number, str(number), name):
                with self.subTest(value=value):
                    self.assertEqual(_sharing_setting_value('DefaultSharingLinkType', value), expected)
        self.assertEqual(_sharing_setting_value('DefaultSharingLinkType', 'SpecificPeople'), 'Specific people')

    def test_boolean_settings_describe_enabled_state_without_claiming_enforcement(self):
        for key in ('RequireAcceptingAccountMatchInvitedAccount', 'PreventExternalUsersFromResharing',
                    'ExternalUserExpirationRequired', 'LegacyAuthProtocolsEnabled'):
            for value, expected in ((True, 'Enabled'), (False, 'Disabled'), (1, 'Enabled'),
                                    (0, 'Disabled'), ('true', 'Enabled'), ('False', 'Disabled')):
                with self.subTest(key=key, value=value):
                    self.assertEqual(_sharing_setting_value(key, value), expected)

    def test_actual_collected_numeric_mix_is_rendered_without_bool_collisions(self):
        bundle = {'sharepoint_governance': {'settings': {
            'SharingCapability': 2, 'OneDriveSharingCapability': 1,
            'DefaultSharingLinkType': 3, 'LegacyAuthProtocolsEnabled': True,
            'PreventExternalUsersFromResharing': False, 'RequireAnonymousLinksExpireInDays': 5,
        }}}
        original = copy.deepcopy(bundle)
        rendered = _settings(bundle)
        for label, expected in (
            ('SharePoint external sharing', 'Anyone links, new guests, and existing guests'),
            ('OneDrive external sharing', 'New and existing guests; no Anyone links'),
            ('Default sharing link', 'Anyone link'),
            ('Legacy authentication protocols', 'Enabled'),
            ('Prevent guests from sharing again', 'Disabled'),
            ('Anyone link expiry', '5 days'),
        ):
            self.assertIn(f'<td>{label}</td><td>{expected}</td>', rendered)
        self.assertNotIn('Enforced', rendered)
        self.assertEqual(bundle, original)

    def test_expiry_zero_and_one_are_days_not_false_and_true(self):
        for value, expected in ((0, 'No expiry requirement (0 days)'), (1, '1 day'),
                                ('0', 'No expiry requirement (0 days)'), ('730', '730 days')):
            self.assertEqual(_sharing_setting_value('RequireAnonymousLinksExpireInDays', value), expected)

    def test_missing_and_unknown_values_are_explicit_without_crashing_or_inventing_labels(self):
        self.assertEqual(_settings({}), '')
        for value in (None, '', ' '):
            self.assertEqual(_sharing_setting_value('DefaultSharingLinkType', value), 'Not reported')
        for key, value in (
            ('SharingCapability', True), ('SharingCapability', False), ('SharingCapability', 99),
            ('DefaultSharingLinkType', 'Unexpected'), ('LegacyAuthProtocolsEnabled', 2),
            ('RequireAnonymousLinksExpireInDays', True), ('RequireAnonymousLinksExpireInDays', -1),
            ('RequireAnonymousLinksExpireInDays', 731), ('DefaultSharingLinkType', {}),
        ):
            with self.subTest(key=key, value=value):
                self.assertTrue(_sharing_setting_value(key, value).startswith('Unmapped value ('))

    def test_unknown_source_text_is_html_escaped(self):
        rendered = _settings({'sharepoint_governance': {'settings': {
            'DefaultSharingLinkType': '<script>alert(1)</script>'}}})
        self.assertIn('Unmapped value (&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)


if __name__ == '__main__':
    unittest.main()

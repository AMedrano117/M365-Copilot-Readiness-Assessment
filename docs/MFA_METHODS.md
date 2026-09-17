# MFA method strength and defaults

[Documentation index](README.md) | [Project overview](../README.md)

The customer HTML includes **MFA method strength and defaults** in the identity assessment
area. The workbook provides **Authentication Coverage**, **Authentication Methods**,
**MFA Preferences**, and **MFA Populations**. Counts are aggregates; these new views do
not include names, email addresses, phone numbers, or user identifiers.

## Collection and replay

The existing live collector reads the paginated Microsoft Graph v1.0
`/reports/authenticationMethods/userRegistrationDetails` report. Its raw rows already
contain registration, capability, administrator, user type, and second-factor preference
fields. No additional endpoint, PDF export, or per-user authentication-method request is
needed for this view.

The API uses the existing **AuditLog.Read.All** application permission with administrator
consent. Microsoft documents Entra ID P1/P2 licensing for authentication usage and insights.
The registration report excludes disabled and soft-deleted users and may take around
36 hours to refresh. Original report update dates are retained; rebuilding does not refresh
them. [API permissions](https://learn.microsoft.com/en-us/graph/api/authenticationmethodsroot-list-userregistrationdetails?view=graph-rest-1.0)
and [report scope and refresh](https://learn.microsoft.com/en-us/entra/identity/authentication/howto-authentication-methods-activity).

Rebuild a collection that contains the raw registration rows with:

```powershell
python main.py --mode offline --collection-input "<saved-collection.json>" --open-html-report
```

Collections containing only old summary totals cannot reconstruct methods or preferences.
Their missing breakdown stays unavailable. A future authorized live run can collect it.

## Interpretation

| Registered credential | Interpretation |
| --- | --- |
| FIDO2/passkeys, Windows Hello for Business, macOS platform credentials | Phishing-resistant registration |
| Authenticator push; software or hardware OATH codes | MFA options that remain susceptible to phishing |
| Authenticator passwordless phone sign-in | Passwordless, but not phishing-resistant |
| SMS or voice phone methods | Lower-assurance methods; identify migration candidates |
| Email or security questions | Recovery/guest context; not workforce MFA |
| Temporary Access Pass | Temporary onboarding/recovery; not a long-term credential |
| Certificate-based authentication | Requires separate verification of multifactor certificate configuration |
| Unrecognized values or ambiguous legacy Authenticator values | Classification unknown; review required |

These categories follow [Microsoft's authentication-method overview](https://learn.microsoft.com/en-us/entra/identity/authentication/overview-authentication)
and [authentication strengths](https://learn.microsoft.com/en-us/entra/identity/authentication/concept-authentication-strengths).

**Phone-only MFA registration** means a user is reported as MFA-registered, has a phone
method, and has no recognized reusable non-phone MFA method in the returned inventory.
Unknown methods prevent that classification. Email and temporary onboarding credentials
do not count as stronger reusable MFA. A mobile-phone registration does not distinguish
whether SMS or voice is actually used, or whether policy permits either.

Counts use unique returned users. Method rows overlap when a person has several methods;
percentages use users with known inventories. Missing fields and conflicting duplicate rows
stay unknown. Member and guest counts are separate; administrators overlap those populations.
Capability is distinct from registration: a registered method can be disabled by policy.

## Default and preferred methods

The report retains three views:

1. **User-selected:** the person's selected default second factor.
2. **System-preferred:** the returned preferred methods for users with system preference enabled.
3. **Preferred in report:** system methods when enabled, otherwise the user-selected method.

If system preference is enabled but its list is missing, the report leaves the preferred
route unknown. It does not silently substitute the user default. Multiple system-preferred
methods can be reported for one user; the SMS/voice headline counts that person once.
See [Graph field definitions](https://learn.microsoft.com/en-us/graph/api/resources/userregistrationdetails?view=graph-rest-1.0).

Preferences are not evidence of the method used in a particular sign-in and do not list
every passwordless first-factor option. Method usage and effective Conditional Access
authentication strengths need separate validation. These aggregates do not independently
approve a readiness stage. Use them to prioritize administrators and phone-only users,
plan phishing-resistant enrollment, and review phone fallback against effective policy.

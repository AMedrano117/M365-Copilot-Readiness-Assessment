"""Tenant-wide configuration components for the pilot decision (methodology 4.0).

A pilot group is chosen from the tenant's own users, so a control that is
enforced for all users covers any pilot group. The assessment therefore judges
each required control on tenant-wide configuration and never needs the pilot
roster. Each check reads collected configuration and returns explicit rows:

* ``Assurance`` - the control is met tenant-wide (the control passes);
* ``Action`` - a tenant condition to fix (the control fails);
* ``Coverage`` - the configuration was not readable, or it is scoped to groups
  the tool cannot map to users; confirm it before the pilot starts.

Checks never infer a result from a license, a policy name or a policy that is
disabled, in simulation or report-only mode. Rows from checks whose source was
not collected are left to the shared gap logic, which explains how to collect it.
"""

import json
import re

from .new_recommendation import new_recommendation
from .source_evidence import (
    envelope_availability, envelope_complete, envelope_flag, resolve_source, source_availability, source_is_complete,
)

OFFICE_365_APP_IDS = {"Office365", "00000003-0000-0ff1-ce00-000000000000"}
LEGACY_CLIENTS = {"exchangeActiveSync", "other"}
M365_DLP_LOCATIONS = (
    ("ExchangeLocation", "Exchange email"), ("SharePointLocation", "SharePoint"),
    ("OneDriveLocation", "OneDrive"), ("TeamsLocation", "Teams chat"),
)
COPILOT_DLP_LOCATION = "copilot.m365"
CONTROL_DOMAINS = {"IDENTITY": "identity", "CONTENT": "content", "DATA": "data_protection",
                   "APPS": "applications", "ENDPOINT": "endpoints", "THREAT": "endpoints", "LICENSE": "licensing"}


def _get(obj, key, default=None):
    """Read a Graph/PowerShell field from a dict or a decoded object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    return default if value is None else value


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _names(items, limit=3):
    items = [str(item) for item in items if item]
    shown = ", ".join(f"'{item}'" for item in items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _row(service, control_id, feature, observation, recommendation, *, disposition, priority="Medium",
         finding_key, evidence_key, source, complete=True, scope, date="", stage=None):
    status = {"Assurance": "Success", "Action": "Action Required", "Coverage": "Not Assessed"}[disposition]
    row = new_recommendation(
        service, feature, observation, recommendation, priority=priority, status=status,
        disposition=disposition, finding_key=finding_key, evidence_key=evidence_key,
        evidence_basis="Tenant-wide configuration" if disposition != "Coverage" else "Not verified",
        confidence="High" if disposition != "Coverage" else "Medium",
    )
    row.update(ControlId=control_id, DomainId=CONTROL_DOMAINS[control_id.split(".")[0]], EvidenceSource=source,
               EvidenceComplete=complete, EvidenceScope=scope, ObservationDate=date or "", BaselineCheck=True)
    if stage:
        row["ReadinessStage"] = stage
    return row


# --- Identity: Conditional Access and security defaults -------------------------------

def conditional_access_facts(policies, strengths=None):
    """Classify Conditional Access policies by what they enforce for everyone."""
    facts = {"enforced_mfa_all": [], "report_only_mfa_all": [], "enforced_mfa_scoped": [],
             "legacy_block_all": [], "report_only_legacy_block": [], "mfa_all_exclusions": 0, 'scope_unknown':[]}
    strengths = {str(_get(row,'id','')):row for row in (strengths or [])}
    for policy in _list(policies):
        state = str(_get(policy, "state", "")).lower()
        name = _get(policy, "displayName", "") or "Unnamed policy"
        conditions = _get(policy, "conditions", {}) or {}
        users = _get(conditions, "users", {}) or {}
        applications = _get(conditions, "applications", {}) or {}
        grant = _get(policy, "grantControls", {}) or {}
        controls = {str(item) for item in _list(_get(grant, "builtInControls", []))}
        all_users = "All" in _list(_get(users, "includeUsers", []))
        apps = set(map(str, _list(_get(applications, "includeApplications", []))))
        all_apps = 'All' in apps and not _list(_get(applications,'excludeApplications',[]))
        strength = _get(grant,'authenticationStrength',{}) or {}
        strength_id = str(_get(strength,'id',''))
        strength_mfa = str(_get(strengths.get(strength_id, strength),'requirementsSatisfied','')).lower() == 'mfa' or strength_id in {
            '00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000004'}
        requires_mfa = ('mfa' in controls or strength_mfa) and (
            str(_get(grant,'operator','AND')).upper()!='OR' or controls <= {'mfa'})
        clients = set(map(str, _list(_get(conditions, "clientAppTypes", []))))
        exclusions = any(_list(_get(users,key,[])) for key in ('excludeUsers','excludeGroups','excludeRoles')) or bool(_get(users,'excludeGuestsOrExternalUsers'))
        restricted = any(_get(conditions,key) for key in ('locations','platforms','devices','signInRiskLevels','userRiskLevels','clientApplications','authenticationFlows'))
        mfa_clients = not clients or 'all' in clients
        blocks_legacy = 'block' in controls and LEGACY_CLIENTS <= clients and not exclusions and not restricted and all_apps
        enforced = state == "enabled"
        report_only = state in {"enabledforreportingbutnotenforced", "enabled_for_reporting_but_not_enforced"}
        authentication_policy = ('mfa' in controls or bool(strength_id)
                                 or 'block' in controls and bool(LEGACY_CLIENTS.intersection(clients)))
        if authentication_policy and enforced and all_users and (exclusions or restricted or requires_mfa and not mfa_clients or strength_id and not strength_mfa or not all_apps):
            facts['scope_unknown'].append(name)
        all_users = all_users and not exclusions and not restricted
        requires_mfa = requires_mfa and mfa_clients
        if requires_mfa and all_users and all_apps:
            if enforced:
                facts["enforced_mfa_all"].append(name)
                facts["mfa_all_exclusions"] += (len(_list(_get(users, "excludeUsers", [])))
                                                + len(_list(_get(users, "excludeGroups", []))))
            elif report_only:
                facts["report_only_mfa_all"].append(name)
        elif requires_mfa and enforced and all_apps:
            facts["enforced_mfa_scoped"].append(name)
        if blocks_legacy and all_users:
            if enforced:
                facts["legacy_block_all"].append(name)
            elif report_only:
                facts["report_only_legacy_block"].append(name)
    return facts


IDENTITY_SCOPE = "Conditional Access policies and security defaults for the assessed tenant"
IDENTITY_FEATURE = "Require MFA and block legacy sign-in for all users"
BUILT_IN_MFA_STRENGTHS = {'00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000003',
                          '00000000-0000-0000-0000-000000000004'}
REPORT_ONLY_STATES = {"enabledforreportingbutnotenforced", "enabled_for_reporting_but_not_enforced"}
RESTRICTING_CONDITIONS = ('locations', 'platforms', 'devices', 'signInRiskLevels', 'userRiskLevels',
                          'clientApplications', 'authenticationFlows')
EMERGENCY_NAME = re.compile(r'break[ _-]?glass|emergency', re.I)
EMERGENCY_SOURCE_TYPES = {'configuration', 'customer_declaration', 'account_metadata'}
ALL = object()


def _source_note(availability, state=None):
    code = (state or {}).get('status_code')
    return str(availability or 'unknown').replace('_', ' ') + (f' (HTTP {code})' if code else '')


def security_defaults_state(client):
    """Enabled or disabled only from an explicit Boolean in a completed read.

    A failed, partial, unrequested or unretained read is an evidence gap and is
    never described as security defaults being off.
    """
    defaults = getattr(client, 'security_defaults', {}) or {}
    value = _get(defaults, 'is_enabled', _get(defaults, 'isEnabled'))
    state, association = resolve_source(getattr(client, 'collection_status', {}) or {}, 'security_defaults')
    if association == 'matched':
        availability = envelope_availability(state)
        if envelope_complete(state) and isinstance(value, bool):
            return {'state': 'enabled' if value else 'disabled', 'availability': 'available', 'note': 'read'}
        availability = 'unresolved' if availability == 'available' else availability
        return {'state': 'unknown', 'availability': availability, 'note': _source_note(availability, state)}
    if association == 'ambiguous':
        return {'state': 'unknown', 'availability': 'ambiguous', 'note': 'ambiguous'}
    # Older saves recorded only the decoded payload flag.
    if envelope_flag(_get(defaults, 'available')) is True and isinstance(value, bool):
        return {'state': 'enabled' if value else 'disabled', 'availability': 'available', 'note': 'read'}
    return {'state': 'unknown', 'availability': 'not_retained', 'note': 'not retained'}


def _policy_terms(policy, strengths):
    conditions = _get(policy, "conditions", {}) or {}
    users = _get(conditions, "users", {}) or {}
    applications = _get(conditions, "applications", {}) or {}
    grant = _get(policy, "grantControls", {}) or {}
    controls = {str(item) for item in _list(_get(grant, "builtInControls", []))}
    strength = _get(grant, 'authenticationStrength', {}) or {}
    strength_id = str(_get(strength, 'id', '') or '')
    strength_mfa = (str(_get(strengths.get(strength_id, strength), 'requirementsSatisfied', '')).lower() == 'mfa'
                    or strength_id in BUILT_IN_MFA_STRENGTHS)
    mfa = 'mfa' in controls or strength_mfa
    operator = str(_get(grant, 'operator', 'AND') or 'AND').upper()
    alternatives = sorted(controls - {'mfa'}) if operator == 'OR' and mfa else []
    clients = set(map(str, _list(_get(conditions, "clientAppTypes", []))))
    include_users = {str(item) for item in _list(_get(users, 'includeUsers', []))}
    exclude_users = {str(item) for item in _list(_get(users, 'excludeUsers', []))}
    state = str(_get(policy, "state", "")).lower()
    return {
        'name': _get(policy, "displayName", "") or "Unnamed policy", 'state': state,
        'enforced': state == 'enabled', 'report_only': state in REPORT_ONLY_STATES,
        'mfa': mfa and not alternatives, 'mfa_alternative': bool(alternatives), 'alternatives': alternatives,
        'unknown_strength': bool(strength_id) and not strength_mfa,
        'legacy_block': 'block' in controls and LEGACY_CLIENTS <= clients,
        'legacy_related': 'block' in controls and bool(LEGACY_CLIENTS & clients),
        'all_clients': not clients or 'all' in clients,
        'all_apps': ('All' in set(map(str, _list(_get(applications, "includeApplications", []))))
                     and not _list(_get(applications, 'excludeApplications', []))),
        'restricted': any(_get(conditions, key) for key in RESTRICTING_CONDITIONS),
        'include_all': 'All' in include_users,
        'include_users': include_users - {'All', 'None', 'GuestsOrExternalUsers'},
        'include_groups': {str(item) for item in _list(_get(users, 'includeGroups', []))},
        'include_roles': bool(_list(_get(users, 'includeRoles', []))),
        'include_guests': 'GuestsOrExternalUsers' in include_users or bool(_get(users, 'includeGuestsOrExternalUsers')),
        'exclude_users': exclude_users - {'GuestsOrExternalUsers'},
        'exclude_groups': {str(item) for item in _list(_get(users, 'excludeGroups', []))},
        'exclude_roles': bool(_list(_get(users, 'excludeRoles', []))),
        'exclude_guests': 'GuestsOrExternalUsers' in exclude_users or bool(_get(users, 'excludeGuestsOrExternalUsers')),
    }


def _datasets(client, name):
    return [dataset for dataset in (getattr(client, 'assessment_datasets', {}) or {}).get(name, []) or []
            if isinstance(dataset, dict)]


def user_population(client):
    """Account IDs from a complete user inventory, or None when it was not established."""
    datasets = _datasets(client, 'user_signin_activity')
    names = {str(_get(row, 'id')): str(_get(row, 'displayName') or '')
             for dataset in datasets for row in dataset.get('records') or [] if _get(row, 'id')}
    if datasets and all(envelope_complete(dataset.get('source') or {}) for dataset in datasets) and names:
        return set(names), names
    return None, names


def group_members(client):
    """Complete transitive user membership per group; incomplete groups stay unknown."""
    members, incomplete = {}, set()
    for dataset in _datasets(client, 'ca_group_members'):
        source = dataset.get('source') or {}
        records = dataset.get('records') or []
        parent = next((str(_get(row, 'ParentId')) for row in records if _get(row, 'ParentId')), None)
        if not parent:
            matched = re.search(r'Parent object (\S+)', str(source.get('scope') or ''))
            parent = matched.group(1) if matched else None
        if not parent:
            continue
        if not envelope_complete(source):
            incomplete.add(parent)
            continue
        members.setdefault(parent, set()).update(
            str(_get(row, 'id')) for row in records
            if _get(row, 'id') and 'group' not in str(_get(row, '@odata.type') or '').lower())
    return {group: ids for group, ids in members.items() if group not in incomplete}


def account_purposes_from_profile(profile):
    """Validated assessment-context account purposes, or none when the context is invalid."""
    if not profile:
        return []
    from .control_reviews import _profile_raw
    from .assessment_catalog import context_validation
    if context_validation(profile):
        return []
    purposes = (_profile_raw(profile).get('assessment_context') or {}).get('account_purposes') or []
    return [row for row in purposes if isinstance(row, dict)]


def _reference_resolves(ref, sources, principal):
    try:
        if type(ref.get('dataset_index')) is not int or type(ref.get('record_index')) is not int:
            return False
        if ref['dataset_index'] < 0 or ref['record_index'] < 0:
            return False
        record = sources[ref['dataset']][ref['dataset_index']]['records'][ref['record_index']]
    except (AttributeError, KeyError, IndexError, TypeError):
        return False
    return _get(record, 'principal_id') in (None, principal)


def validated_emergency_accounts(client, account_purposes):
    """Emergency-access purpose validated from retained declarations, never from names.

    Each declaration needs the account ID, an owner, the authentication design,
    the monitoring expectation, a review date or exception reference, a supported
    source type and evidence references that resolve to retained records.
    """
    declarations = [row for row in account_purposes or [] if row.get('purpose') == 'emergency' and row.get('principal_id')]
    if not declarations:
        return set(), {}
    from .assessment_catalog import collect_assessment_sources
    from .evidence_contract import parse_date
    sources = collect_assessment_sources([client])
    valid, missing = set(), {}
    for row in declarations:
        principal = str(row['principal_id'])
        gaps = [field.replace('_', ' ') for field in ('owner', 'authentication_design', 'monitoring')
                if not str(row.get(field) or '').strip()]
        if not (parse_date(row.get('reviewed_at')) or str(row.get('exception_id') or '').strip()):
            gaps.append('review date or exception reference')
        if row.get('source_type') not in EMERGENCY_SOURCE_TYPES:
            gaps.append('supported evidence source type')
        refs = row.get('evidence_refs') or []
        if not refs or not all(isinstance(ref, dict) and _reference_resolves(ref, sources, principal) for ref in refs):
            gaps.append('retained evidence record')
        if gaps:
            missing.setdefault(principal, []).extend(gaps)
        else:
            valid.add(principal)
    return valid - set(missing), missing


def _coverage(candidates, members, population, emergency):
    """Resolve the accounts an enforced requirement covers without inferring membership."""
    unresolved, all_except, covered = [], [], set()
    for terms in candidates:
        reasons = []
        targets = ALL if terms['include_all'] else set(terms['include_users'])
        if targets is not ALL:
            if terms['include_roles']:
                reasons.append('directory-role assignments')
            if terms['include_guests']:
                reasons.append('guest or external-user types')
            for group in sorted(terms['include_groups']):
                if group in members:
                    targets |= members[group]
                else:
                    reasons.append(f'membership of included group {group} was not retained completely')
        excluded = set(terms['exclude_users'])
        if terms['exclude_roles']:
            reasons.append('excluded directory roles')
        if terms['exclude_guests']:
            reasons.append('excluded guest or external-user types')
        for group in sorted(terms['exclude_groups']):
            if group in members:
                excluded |= members[group]
            else:
                reasons.append(f'membership of excluded group {group} was not retained completely')
        if reasons:
            unresolved.append((terms['name'], reasons))
        elif targets is ALL:
            all_except.append(excluded)
        else:
            covered |= targets - excluded
    result = {'unresolved': unresolved, 'covered': covered, 'uncovered': set(), 'validated': set(),
              'unverified': set(), 'state': None}
    if any(not excluded for excluded in all_except):
        result['state'] = 'EnforcedForAllUsers'
    elif all_except:
        residual = set.intersection(*all_except) - covered
        result.update(validated=residual & emergency, unverified=residual - emergency)
        result['state'] = 'EnforcedWithValidatedEmergencyExclusions' if not result['unverified'] else 'ExclusionReviewRequired'
    elif covered and population is not None:
        uncovered = population - covered - emergency
        result.update(uncovered=uncovered, validated=(population - covered) & emergency)
        result['state'] = ('EnforcedForAllUsers' if not uncovered else
                           'ScopeNotEstablished' if unresolved else 'NotEnforcedForAllUsers')
    elif covered or unresolved:
        result['state'] = 'ScopeNotEstablished'
        if covered and not unresolved:
            unresolved.append(('user and group assignments', ['a complete user inventory was not retained']))
    return result


def _requirement_policies(kind, terms):
    if kind == 'mfa':
        candidates = [t for t in terms if t['enforced'] and t['mfa'] and t['all_apps'] and t['all_clients']
                      and not t['restricted'] and not t['unknown_strength']]
        alternatives = [t for t in terms if t['enforced'] and t['mfa_alternative'] and t['all_apps'] and t['all_clients']
                        and not t['restricted']]
        other = [t for t in terms if t['enforced'] and t['include_all'] and (t['mfa'] or t['mfa_alternative'] or t['unknown_strength'])
                 and t not in candidates and t not in alternatives]
        report_only = [t['name'] for t in terms if t['report_only'] and t['mfa'] and t['all_apps'] and t['all_clients']
                       and t['include_all'] and not t['restricted']]
    else:
        candidates = [t for t in terms if t['enforced'] and t['legacy_block'] and t['all_apps'] and not t['restricted']]
        alternatives = []
        other = [t for t in terms if t['enforced'] and t['include_all'] and t['legacy_related'] and t not in candidates]
        report_only = [t['name'] for t in terms if t['report_only'] and t['legacy_block'] and t['include_all']
                       and t['all_apps'] and not t['restricted']]
    return candidates, alternatives, other, report_only


def _component(kind, terms, members, population, emergency, names, defaults):
    """One requirement (MFA or legacy blocking): pass, fail, review or evidence gap."""
    label = 'MFA' if kind == 'mfa' else 'legacy-authentication blocking'
    requirement = 'requires MFA for all users and cloud apps' if kind == 'mfa' else 'blocks legacy authentication for all users'
    if defaults['state'] == 'enabled':
        return {'outcome': 'pass', 'state': 'SecurityDefaults', 'text': (
            "Security defaults are enabled, configuring registration and risk-based MFA requirements" if kind == 'mfa'
            else "security defaults block legacy authentication")}
    candidates, alternatives, other, report_only = _requirement_policies(kind, terms)
    coverage = _coverage(candidates, members, population, emergency)
    state = coverage['state']
    names_text = _names([t['name'] for t in candidates])
    if state == 'EnforcedForAllUsers':
        if coverage['covered'] and not any(t['include_all'] for t in candidates):
            text = (f"Enforced Conditional Access policies require {label} for every one of the {len(population)} accounts "
                    f"in the complete user inventory through user and group assignments ({names_text})")
        else:
            text = (f"Conditional Access requires MFA for all users and cloud apps ({names_text})" if kind == 'mfa'
                    else f"legacy authentication is blocked for all users ({names_text})")
        return {'outcome': 'pass', 'state': state, 'text': text}
    if state == 'EnforcedWithValidatedEmergencyExclusions':
        count = len(coverage['validated'])
        lead = 'Conditional Access requires MFA for all users and cloud apps' if kind == 'mfa' else 'legacy authentication is blocked for all users'
        return {'outcome': 'pass', 'state': state, 'text': (
            f"{lead} ({names_text}) except {count} documented emergency-access account{'s' if count != 1 else ''} with owner, "
            "authentication design, monitoring and review evidence")}
    if state == 'ExclusionReviewRequired':
        unverified = coverage['unverified']
        by_name = sum(bool(EMERGENCY_NAME.search(names.get(identifier, ''))) for identifier in unverified)
        text = (f"{names_text} {requirement} but excludes {len(unverified)} account(s) whose emergency-access purpose "
                "is not validated by retained evidence")
        text += (f"; {by_name} of them have emergency-access style names, but name patterns only identify candidates and do not validate purpose"
                 if by_name else "; name patterns were not used to validate purpose")
        if coverage['validated']:
            text += f"; {len(coverage['validated'])} other excluded account(s) are documented emergency-access accounts"
        return {'outcome': 'review', 'state': state, 'text': text}
    if alternatives:
        alternative = sorted({control for t in alternatives for control in t['alternatives']})
        return {'outcome': 'review', 'state': 'AlternativeGrantPathRequiresReview', 'text': (
            f"{_names([t['name'] for t in alternatives])} allows MFA or an alternative grant control ({', '.join(alternative)}) for all users "
            "and cloud apps. A user who satisfies the alternative is not required to complete MFA, so universal MFA enforcement is not "
            "established. Device-compliance coverage, platforms and client conditions require review")}
    if state == 'ScopeNotEstablished' or other:
        reasons = '; '.join(f"{name}: {', '.join(items)}" for name, items in coverage['unresolved'])
        if other:
            reasons = '; '.join(filter(None, [reasons, 'conditions, application scope, client types or authentication strength of '
                                              + _names([t['name'] for t in other])]))
        return {'outcome': 'review', 'state': 'ScopeNotEstablished', 'text': (
            f"Coverage of all users by enforced {label} policies cannot be established from the retained evidence ({reasons}). "
            "Group scope is never inferred from names; membership must be retained completely")}
    if state == 'NotEnforcedForAllUsers':
        targeted = len(population) - len(coverage['uncovered']) - len(coverage['validated'])
        text = (f"Enforced Conditional Access policies require {label} only for specific users or groups: {targeted} of {len(population)} "
                f"accounts in the complete user inventory are targeted ({names_text}) and {len(coverage['uncovered'])} accounts are not "
                f"targeted by an enforced {label} policy. The count includes every inventory account, including any disabled, guest or "
                "resource accounts")
    elif kind == 'mfa':
        text = "No enforced Conditional Access policy requires MFA for all users and cloud apps"
        if report_only:
            text += f". {_names(report_only)} would do so but {'is' if len(report_only) == 1 else 'are'} in report-only mode (not enforced)"
        state = 'NoEnforcedPolicy'
    else:
        text = "No enforced policy blocks legacy authentication for all users"
        if report_only:
            text += f" ({_names(report_only)} is report-only)"
        state = 'NoEnforcedPolicy'
    if defaults['state'] == 'disabled':
        if kind == 'mfa':
            text += ", and security defaults are disabled"
        return {'outcome': 'fail', 'state': state, 'text': text, 'report_only': report_only}
    return {'outcome': 'gap', 'state': 'SecurityDefaultsUnknown', 'text': (
        text + f". The security defaults setting was not established (read: {defaults['note']}), so tenant-wide {label} "
        "is not established; this is not a measured control failure")}


def _identity_source_gap(entra_client, defaults, ca_state, common):
    availability = envelope_availability(ca_state)
    note = _source_note(availability, ca_state)
    known = defaults['state'] if defaults['state'] != 'unknown' else 'not established'
    row = _row("Entra", "IDENTITY.AUTH", IDENTITY_FEATURE,
               f"Conditional Access policies were {note} at collection time, so tenant-wide MFA and legacy-authentication "
               f"coverage is not established. Security defaults: {known}. This is an evidence gap, not a measured control failure.",
               "Read the complete Conditional Access policy inventory (Microsoft Graph Policy.Read.All) and rebuild, or record a dated "
               "review of the enforced MFA and legacy-authentication policies, their assignments and exclusions.",
               disposition="Coverage", finding_key="baseline.identity.source", source="ca_policies", complete=False, **common)
    row.update(SemanticCondition=f'ca_policies={availability}; security_defaults={defaults["state"]}', ReadinessTreatment='EvidenceGap',
               SourceStates=f"ca_policies={note}; security_defaults={defaults['note']}", SourceAvailability=availability,
               FindingCondition='mfa.enforcement_not_established',
               CompletionEvidence='A complete Conditional Access policy read, or a dated review of the enforced policies and their scope.')
    return row


def assess_identity_baseline(entra_client, collected_at="", account_purposes=None):
    """Tenant-wide MFA and legacy-authentication result from policies and source state."""
    if entra_client is None:
        return []
    defaults = security_defaults_state(entra_client)
    ca_state, ca_association = resolve_source(getattr(entra_client, 'collection_status', {}) or {}, 'ca_policies')
    common = dict(evidence_key="conditional_access_detail", scope=IDENTITY_SCOPE, date=collected_at)
    if not source_is_complete(entra_client, "ca_policies"):
        if defaults['state'] == 'enabled':
            row = _row("Entra", "IDENTITY.AUTH", IDENTITY_FEATURE,
                       "Security defaults are enabled, configuring registration, risk-based MFA requirements and legacy authentication blocking. "
                       "Returned sign-ins or a tested operational review must establish observed protection.", "",
                       disposition="Assurance", priority="Low", finding_key="baseline.identity.sign_in",
                       source="security_defaults", **common)
            row.update(SemanticCondition='mfa=SecurityDefaults; legacy=SecurityDefaults', ReadinessTreatment='Pass',
                       SourceStates=f"ca_policies={_source_note(source_availability(entra_client, 'ca_policies'), ca_state)}; security_defaults=enabled",
                       FindingCondition='authentication.protection')
            return [row]
        # Without a recorded request outcome, the shared gap logic explains how to collect it.
        return [_identity_source_gap(entra_client, defaults, ca_state, common)] if ca_association == 'matched' else []
    from .expanded_collection import dataset_rows
    policies = _list(getattr(entra_client, "ca_policies", []) or [])
    strengths = {str(_get(row, 'id', '')): row for row in dataset_rows(entra_client, 'authentication_strengths') or []}
    terms = [_policy_terms(policy, strengths) for policy in policies]
    population, names = user_population(entra_client)
    members = group_members(entra_client)
    emergency, _ = validated_emergency_accounts(entra_client, account_purposes)
    mfa = _component('mfa', terms, members, population, emergency, names, defaults)
    legacy = _component('legacy', terms, members, population, emergency, names, defaults)
    outcomes = {mfa['outcome'], legacy['outcome']}
    semantic = f"mfa={mfa['state']}; legacy={legacy['state']}"
    sources = (f"ca_policies=complete ({len(policies)} policies); security_defaults="
               + (defaults['state'] if defaults['state'] != 'unknown' else defaults['note']))
    observation = ("; ".join(part['text'] for part in (mfa, legacy)) + ". The complete Conditional Access read returned "
                   f"{len(policies)} Conditional Access polic{'y' if len(policies) == 1 else 'ies'}.")
    if outcomes == {'pass'}:
        row = _row("Entra", "IDENTITY.AUTH", IDENTITY_FEATURE, observation, "", disposition="Assurance", priority="Low",
                   finding_key="baseline.identity.sign_in", source="ca_policies", **common)
        row.update(ReadinessTreatment='Pass', FindingCondition='authentication.protection')
    elif 'fail' in outcomes:
        fixes = []
        if mfa['outcome'] == 'fail':
            if mfa.get('report_only'):
                fixes.append("review the report-only sign-in results, then switch the all-users MFA policy to On, excluding only documented emergency-access accounts")
            elif mfa['state'] == 'NotEnforcedForAllUsers':
                fixes.append("extend the enforced MFA requirement to all users and cloud apps, excluding only documented emergency-access accounts")
            else:
                fixes.append("create a Conditional Access policy that requires MFA for all users and all cloud apps, excluding only documented emergency-access accounts")
        if legacy['outcome'] == 'fail':
            fixes.append("block legacy authentication (Exchange ActiveSync and other clients) for all users")
        if outcomes - {'fail', 'pass'}:
            fixes.append("review the other component described above before treating it as covered")
        recommendation = ("Before the pilot, " + "; and ".join(fixes) + ". Copilot acts with the signed-in user's access, "
                          "so every account needs strong sign-in protection.")
        row = _row("Entra", "IDENTITY.AUTH", IDENTITY_FEATURE, observation, recommendation, disposition="Action",
                   priority="High" if mfa['outcome'] == 'fail' else "Medium", finding_key="baseline.identity.sign_in",
                   source="ca_policies", **common)
        row.update(ReadinessTreatment='Remediate', FindingCondition='authentication.protection',
                   CompletionEvidence='A dated Conditional Access export showing the enforced all-users policies and their exclusions, '
                                      'and sign-in results showing MFA and legacy-client blocking.')
    elif 'review' in outcomes:
        recommendation = ("Review the assignments, grant controls, exclusions and population described above before changing enforcement. "
                          "Record each excluded emergency-access account's purpose, owner, authentication design, monitoring and review in the "
                          "assessment profile account purposes, retain complete group membership and the user inventory, or require MFA "
                          "for all users. Record dependencies and user-impact risks.")
        row = _row('Entra', 'IDENTITY.AUTH', IDENTITY_FEATURE, observation, recommendation, disposition='Coverage',
                   finding_key='baseline.identity.scope', source='ca_policies', **common)
        row.update(ReadinessTreatment='ScopeReviewRequired',
                   FindingCondition=('conditional_access.exclusions' if 'ExclusionReviewRequired' in semantic else
                                     'conditional_access.policy_coverage' if 'AlternativeGrantPath' in semantic else
                                     'mfa.enforcement_not_established'),
                   CompletionEvidence='A dated review of the effective population covered by each enforced policy, including exclusions and grant alternatives.')
    else:
        row = _row('Entra', 'IDENTITY.AUTH', IDENTITY_FEATURE, observation,
                   "Read the security defaults policy (Microsoft Graph Policy.Read.All) and rebuild, or record its state from the Microsoft Entra "
                   "admin center with the date. Then confirm that an enforced policy or security defaults require MFA and block legacy authentication.",
                   disposition='Coverage', finding_key='baseline.identity.source', source='ca_policies', **common)
        row.update(ReadinessTreatment='EvidenceGap', FindingCondition='mfa.enforcement_not_established',
                   CompletionEvidence='A completed security defaults read or a dated record of the setting.')
    row.update(SemanticCondition=semantic, SourceStates=sources)
    return [row]


def enforced_mfa_policy_count(policies):
    """Enabled (enforced) policies that require MFA; report-only and disabled policies do not count."""
    count = 0
    for policy in _list(policies):
        grant = _get(policy, "grantControls", {}) or {}
        controls = {str(item) for item in _list(_get(grant, "builtInControls", []))}
        strength = _get(grant, 'authenticationStrength', {}) or {}
        known_mfa = str(_get(strength, 'requirementsSatisfied', '')).lower() == 'mfa' or str(_get(strength, 'id', '')) in {
            '00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000004'}
        mandatory = str(_get(grant, 'operator', 'AND')).upper() != 'OR' or controls <= {'mfa'}
        if str(_get(policy, "state", "")).lower() == "enabled" and mandatory and ('mfa' in controls or known_mfa):
            count += 1
    return count


# --- Data protection: DLP, label publishing and retention -------------------------------

def _all_location(value):
    for item in _list(value):
        name = _get(item, "Name", item) if isinstance(item, dict) else item
        if str(name).strip().lower() == "all":
            return True
    return False


def _unified_locations(policy):
    raw = _get(policy, "Locations", "")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            raw = []
    return [item for item in _list(raw) if isinstance(item, dict)]


def dlp_facts(policies):
    facts = {"total": 0, "enabled": 0, "enforced": [], "enforced_tenant": [], "workloads": set(),
             "copilot_enforced": [], "copilot_simulation": [], "scoped_enforced": []}
    for policy in _list(policies):
        if not isinstance(policy, dict) and not hasattr(policy, "__dict__"):
            continue
        facts["total"] += 1
        enabled = _get(policy, "Enabled") is True or str(_get(policy, "Enabled", "")).lower() == "true"
        mode = str(_get(policy, "Mode", "")).lower()
        name = _get(policy, "DisplayName") or _get(policy, "Name") or "Unnamed policy"
        if enabled:
            facts["enabled"] += 1
        unified = _unified_locations(policy)
        copilot_locations = [item for item in unified if COPILOT_DLP_LOCATION in str(_get(item, "Location", "")).lower()]
        copilot = bool(copilot_locations)
        tenant_copilot = any(not _list(_get(item, 'Exclusions', [])) and
            any(_get(inc, 'Identity') == 'All' or _get(inc, 'Type') == 'Tenant'
                for inc in _list(_get(item, 'Inclusions', []))) for item in copilot_locations)
        workloads = {label for field, label in M365_DLP_LOCATIONS if _all_location(_get(policy, field)) and
                     not _list(_get(policy, field + 'Exception', []))}
        if copilot and enabled and mode == "enable":
            facts["copilot_enforced"].append(name)
        elif copilot and enabled:
            facts["copilot_simulation"].append(name)
        if not (enabled and mode == "enable"):
            continue
        facts["enforced"].append(name)
        if workloads or tenant_copilot:
            facts["enforced_tenant"].append(name)
            facts["workloads"].update(workloads)
            if tenant_copilot:
                facts["workloads"].add("Microsoft 365 Copilot")
        elif copilot or any(_list(_get(policy, field)) for field, _ in M365_DLP_LOCATIONS):
            facts["scoped_enforced"].append(name)
    return facts


def _purview_row(client, control_id, feature, observation, recommendation, *, disposition, priority="Medium",
                 finding_key, source, date):
    return _row("Purview", control_id, feature, observation, recommendation, disposition=disposition, priority=priority,
                finding_key=finding_key, evidence_key="purview_policy_detail", source="purview_" + source,
                scope="Tenant policy configuration returned by Purview", date=date)


def assess_data_protection_baseline(purview_client, collected_at=""):
    if purview_client is None:
        return []
    provenance = getattr(purview_client, "cache_provenance", {}) or {}
    date = provenance.get("collected_at") or collected_at or ""
    rows = []

    dlp = getattr(purview_client, "dlp_policies", {}) or {}
    policies = _list(dlp.get("policies") if isinstance(dlp, dict) else [])
    if source_is_complete(purview_client, "dlp_policies", dlp) and policies:
        facts = dlp_facts(policies)
        if facts["enforced_tenant"]:
            copilot = (f" {len(facts['copilot_enforced'])} enforced polic{'y applies' if len(facts['copilot_enforced']) == 1 else 'ies apply'} "
                       f"to Microsoft 365 Copilot ({_names(facts['copilot_enforced'], 2)})." if facts["copilot_enforced"] else "")
            rows.append(_purview_row(purview_client, "DATA.DLP", "DLP policies configured in enforcement mode",
                f"{len(facts['enforced'])} of {facts['total']} DLP policies are configured in enforcement mode; {len(facts['enforced_tenant'])} apply to all "
                f"users or locations for {', '.join(sorted(facts['workloads']))}.{copilot}", "",
                disposition="Assurance", priority="Low", finding_key="baseline.dlp.enforced", source="dlp_policies", date=date))
            if not facts["copilot_enforced"]:
                opportunity = new_recommendation(
                    "Purview", "Add a DLP policy for Microsoft 365 Copilot",
                    ("No enforced DLP policy targets the Microsoft 365 Copilot location"
                     + (f"; {_names(facts['copilot_simulation'], 2)} {'is' if len(facts['copilot_simulation']) == 1 else 'are'} in simulation mode" if facts["copilot_simulation"] else "")
                     + ". Copilot DLP can stop Copilot from processing files and emails with selected sensitivity labels, and can block prompts that contain sensitive information."),
                    "Review the simulation results, then enforce a DLP policy on the Microsoft 365 Copilot location for the labels and sensitive information types the business agrees on.",
                    priority="Medium", status="Insight", disposition="Opportunity", finding_key="baseline.dlp.copilot_location",
                    evidence_key="purview_policy_detail", evidence_basis="Tenant-wide configuration", confidence="High")
                opportunity.update(ControlId="DATA.DLP", EvidenceSource="purview_dlp_policies", EvidenceComplete=True,
                                   EvidenceScope="Tenant policy configuration returned by Purview", ObservationDate=date)
                rows.append(opportunity)
        elif facts["scoped_enforced"]:
            rows.append(_purview_row(purview_client, "DATA.DLP", "Confirm the scope of enforced DLP policies",
                f"{len(facts['scoped_enforced'])} enforced DLP polic{'y is' if len(facts['scoped_enforced']) == 1 else 'ies are'} limited to specific "
                f"users, groups or sites ({_names(facts['scoped_enforced'])}); none applies to all users.",
                "Confirm that the pilot users and the content they work with are inside these policies' scope, or extend a policy to all users.",
                disposition="Coverage", finding_key="baseline.dlp.scoped", source="dlp_policies", date=date))

    labels = getattr(purview_client, "label_policies", {}) or {}
    label_policies = _list(labels.get("policies") if isinstance(labels, dict) else [])
    if source_is_complete(purview_client, "label_policies", labels) and label_policies:
        active = [policy for policy in label_policies
                  if _get(policy, "Enabled") is not False and str(_get(policy, "Mode", "")).lower() not in {"pendingdeletion", "disable", "disabled"}]
        names = [_get(policy, "Name", "") for policy in active]
        has_scope = any(_get(policy, "ExchangeLocation") is not None or _get(policy, "ExchangeLocationCount") is not None
                        for policy in label_policies)
        to_everyone = [_get(policy, "Name", "") for policy in active if _all_location(_get(policy, "ExchangeLocation")) and
                       not _list(_get(policy, 'ExchangeLocationException', [])) and
                       not _get(policy, 'ExchangeLocationExceptionCount', 0)]
        feature = "Confirm sensitivity labels are published to users"
        if not active:
            rows.append(_purview_row(purview_client, "DATA.PUBLISHING", feature,
                f"None of the {len(label_policies)} label publishing policies is active (disabled or pending deletion), so no labels are available to users.",
                "Publish the agreed sensitivity labels to all users, or at least to the pilot group, with a default label for new content.",
                disposition="Action", finding_key="baseline.labels.published", source="label_policies", date=date))
        elif to_everyone:
            rows.append(_purview_row(purview_client, "DATA.PUBLISHING", "Sensitivity labels are published to all users",
                f"{len(active)} active label publishing polic{'y' if len(active) == 1 else 'ies'}; {_names(to_everyone, 2)} publish{'es' if len(to_everyone) == 1 else ''} labels to all users.", "",
                disposition="Assurance", priority="Low", finding_key="baseline.labels.published", source="label_policies", date=date))
        else:
            reason = ("publish to specific users or groups only" if has_scope else
                      "were collected without their publishing scope (collections made before assessment version 3.0 do not record it)")
            rows.append(_purview_row(purview_client, "DATA.PUBLISHING", feature,
                f"{len(active)} active label publishing polic{'y' if len(active) == 1 else 'ies'} ({_names(names, 2)}) {reason}.",
                "Confirm in the Microsoft Purview portal (Information protection > Publishing policies) that the pilot users receive the labels, "
                "or rerun collection to record the publishing scope.",
                disposition="Coverage", finding_key="baseline.labels.published", source="label_policies", date=date))

    retention = getattr(purview_client, "retention_labels", {}) or {}
    retention_rows = _list(retention.get("labels") if isinstance(retention, dict) else [])
    if source_is_complete(purview_client, "retention_labels", retention):
        enabled = [row for row in retention_rows if _get(row, "Enabled") is not False]
        if enabled:
            rows.append(_purview_row(purview_client, "DATA.RETENTION", "Retention policies are configured",
                f"{len(enabled)} enabled retention polic{'y' if len(enabled) == 1 else 'ies'} ({_names([_get(row, 'Name', '') for row in enabled])}). "
                "Check that one covers Copilot and AI app interactions if the business needs to keep or delete them on a schedule.", "",
                disposition="Assurance", priority="Low", finding_key="baseline.retention.configured", source="retention_policies", date=date))
        else:
            rows.append(_purview_row(purview_client, "DATA.RETENTION", "Decide retention for Copilot interactions and content",
                "No enabled retention policy was returned. Without one, Copilot prompts and responses and business content follow default deletion by users.",
                "Agree with the records or compliance owner how long Copilot interactions and business content must be kept, then configure retention policies. "
                "This is a planning condition; it does not block a pilot.",
                disposition="Action", priority="Medium", finding_key="baseline.retention.configured", source="retention_policies", date=date))
    return rows


# --- Applications and licensing ---------------------------------------------------------

def assess_m365_baseline(m365_client, collected_at=""):
    if m365_client is None:
        return []
    rows = []
    scope = "Assessed tenant"
    if source_is_complete(m365_client, "external_connections"):
        connections = _list(getattr(m365_client, "external_connections", []) or [])
        if not connections:
            rows.append(_row("M365", "APPS.CONNECTIONS", "No Graph connector connections returned",
                "Microsoft Graph returned no external connections in this connector inventory. Other agent integrations and connected sources require separate review; this inventory alone does not establish Copilot's complete content boundary.", "",
                disposition="Assurance", priority="Low", finding_key="baseline.connectors.inventory",
                evidence_key="external_connection_detail", source="m365_external_connections", scope=scope, date=collected_at))
        else:
            names = [_get(item, "name") or _get(item, "id", "") for item in connections]
            rows.append(_row("M365", "APPS.CONNECTIONS", "Review Copilot connectors before the pilot",
                f"{len(connections)} Copilot connector(s) bring external content into Microsoft 365 search and Copilot ({_names(names)}).",
                "Confirm each connector's owner, the content it indexes and how item permissions are mapped, so pilot users only see external content they are entitled to.",
                disposition="Coverage", finding_key="baseline.connectors.inventory",
                evidence_key="external_connection_detail", source="m365_external_connections", scope=scope, date=collected_at))
    coverage = getattr(m365_client, "license_coverage", {}) or {}
    capacity = _get(coverage, "copilot_subscription_capacity", {}) or {}
    if _get(capacity, "available") is True:
        seats = _get(capacity, "enabled_seats", 0) or 0
        assigned = _get(capacity, "assigned_seats", 0) or 0
        if seats > 0:
            rows.append(_row("M365", "LICENSE.ASSIGNMENT", "Copilot licenses are available",
                f"The tenant has {seats} Microsoft 365 Copilot license(s): {assigned} assigned and {max(seats - assigned, 0)} unassigned.",
                "", disposition="Assurance", priority="Low", finding_key="baseline.license.capacity",
                evidence_key="copilot_readiness_detail", source="m365_license_coverage", scope=scope, date=collected_at))
        else:
            rows.append(_row("M365", "LICENSE.ASSIGNMENT", "Acquire Copilot licenses for the pilot",
                "No Microsoft 365 Copilot subscription was found in the tenant.",
                "Purchase or trial enough Microsoft 365 Copilot licenses for the pilot group, and assign them through a group. "
                "This is a procurement step, not a security condition.",
                disposition="Coverage", finding_key="baseline.license.capacity",
                evidence_key="copilot_readiness_detail", source="m365_license_coverage", scope=scope, date=collected_at))
    return rows


def assess_tenant_baseline(*, m365_client=None, entra_client=None, purview_client=None, collected_at="", account_purposes=None):
    """All tenant-wide baseline rows for the available clients."""
    return [
        *assess_identity_baseline(entra_client, collected_at, account_purposes=account_purposes),
        *assess_data_protection_baseline(purview_client, collected_at),
        *assess_m365_baseline(m365_client, collected_at),
    ]

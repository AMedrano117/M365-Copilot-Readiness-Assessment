"""Deterministic checks of raw control evidence, shared by collection and replay."""

from .new_recommendation import new_recommendation
from .source_evidence import source_is_complete, source_availability, SOURCE_ALIASES
import re


def _lockbox_investigation(client, payload, state):
    """Declare only an explicitly retained setting, never a missing-field default."""
    provenance = getattr(client, 'cache_provenance', {}) or {}
    source = {
        'name': 'Purview organization configuration',
        'cmdlet': 'Get-OrganizationConfig',
        'collected_at': state.get('collected_at') or state.get('collection_completed_at') or provenance.get('collected_at') or getattr(client, 'collected_at', '') or 'Not retained',
        'file': state.get('source_file') or provenance.get('source_file') or 'Not retained',
        'scope': 'Organization-level Customer Lockbox setting; no support-access request records are implied',
        'window': 'Configuration snapshot at collection time',
        'availability': source_availability(client, 'org_config', payload),
        'complete': source_is_complete(client, 'org_config', payload),
        'limitations': 'The setting does not establish contractual need, licensing entitlement, approval workflow effectiveness or individual support access.',
    }
    candidates = []
    if isinstance(payload, dict):
        for container in ('data', 'raw_data', 'raw'):
            if isinstance(payload.get(container), dict):
                candidates.append((f'org_config.{container}', payload[container]))
        candidates.append(('org_config', payload))
    found, invalid, normalized_false = [], [], False
    for path, candidate in candidates:
        for field, original in candidate.items():
            if str(field).replace('_', '').casefold() != 'customerlockboxenabled':
                continue
            if type(original) is bool:
                enabled = original
            elif isinstance(original, str) and original.strip().casefold() in {'true', 'false'}:
                enabled = original.strip().casefold() == 'true'
            else:
                invalid.append(f'{path}.{field}')
                continue
            # Legacy hydration defaulted a missing source field to False. It
            # retained no marker that distinguishes that default from a read.
            if path == 'org_config' and str(field) == 'customer_lockbox_enabled' and enabled is False:
                normalized_false = True
                continue
            found.append((path, str(field), original, enabled))
    if found and len({entry[3] for entry in found}) == 1:
        path, field, original, enabled = found[0]
        return {'kind': 'configuration', 'sheet_name': 'Customer Lockbox Setting',
            'record_id_field': 'Setting ID', 'entity_field': 'Setting', 'status_field': 'Enabled',
            'records': [{'Setting ID': 'org_config.' + field, 'Setting': 'Customer Lockbox',
                         'Source Field': path + '.' + field, 'Original Value': original, 'Enabled': enabled,
                         'Cmdlet': 'Get-OrganizationConfig', 'Source State': source['availability']}],
            'source': source, 'reconciliation': {'operation': 'count', 'expected': 1},
            'reason': 'One explicitly retained organization setting supports this configuration review. No affected-user or support-request population is asserted.'}
    if found:
        reason = 'Retained Customer Lockbox fields disagree; the enabled state cannot be reproduced unambiguously.'
    elif normalized_false:
        reason = ('Only org_config.customer_lockbox_enabled=False was retained. The legacy collector also wrote this value when '
                  'the original CustomerLockBoxEnabled property was missing; the original property was not retained, so disabled cannot be independently established.')
    elif invalid:
        reason = 'The retained Customer Lockbox setting has no explicit Boolean true/false value: ' + ', '.join(invalid) + '.'
    else:
        reason = 'The original organization configuration did not retain a CustomerLockBoxEnabled setting; no default False is treated as a measured disabled state.'
    if state.get('reason'):
        reason += ' Collection qualification: ' + str(state['reason'])
    return {'kind': 'unavailable', 'reason': reason, 'source': source}


def qualify_saved_recommendations(rows, service, client):
    """Revalidate old precomputed conclusions against their saved source outcomes.

    This runs for both live builds and replay. A source that was skipped or failed
    cannot acquire an empty successful result through an older module's defaults.
    Original collection files retain the unchanged source recommendations.
    """
    if client is None:
        # Separately supplied reviewed evidence has no collector payload to
        # re-evaluate. Its own explicit provenance is qualified by the model.
        return [dict(row) for row in rows or []]
    qualified = []
    for original in rows or []:
        row = dict(original)
        if service == 'Entra':
            from .identity_investigation import qualify_identity_recommendation
            row = qualify_identity_recommendation(row, client)
            if str(row.get('Observation') or '').lower().startswith('user consent enabled for applications'):
                from .authentication_methods import _get
                old_observation = str(row.get('Observation') or '')
                permissions = _get(getattr(client, 'authorization_policy', {}) or {}, 'defaultUserRolePermissions') or {}
                assignments = _get(permissions, 'permissionGrantPoliciesAssigned')
                row.setdefault('OriginalObservation', old_observation)
                row.update(FindingKey='entra.apps.user_consent_assignment', EvidenceKey='app_consent_policy_detail',
                           EvidenceSource='authorization_policy', EvidenceComplete=source_is_complete(client, 'authorization_policy'),
                           EvidenceScope='Default-user permission-grant policy assignments and retained conditions for assigned definitions')
                if isinstance(assignments, (list, tuple)):
                    self_consent = [value for value in assignments if str(value).lower().startswith('managepermissiongrantsforself.')]
                    row['Observation'] = (f'The authorization policy assigns {len(self_consent)} self-consent policy(ies) to default users. '
                        'Any permitted user consent is subject to the assigned policies\' include and exclude conditions. '
                        'Assignment does not imply unrestricted approval, consent to every application, or access to Copilot content.')
                    row['Recommendation'] = ('Review the linked assigned-policy conditions and confirm that their publisher, application, resource and permission scope matches the intended consent boundary. '
                        'Use an admin-consent workflow for requests outside that boundary; review individual grants separately before revocation.')
                else:
                    row.update(Observation='The retained authorization policy does not contain default-user permission-grant assignments; the user-consent boundary cannot be reproduced.',
                               Disposition='Coverage', Status='Not Assessed', EvidenceComplete=False,
                               Recommendation='Read or export the default-user consent assignments and their policy conditions, then review the intended approval boundary.')
        text = " ".join(str(row.get(k) or "") for k in ("Feature", "FindingKey", "Observation")).lower()
        observation = str(row.get("Observation") or "")
        if service == 'Entra' and re.search(r'\b(?:use passwordless authentication|have a (?:recognized )?passwordless method registered)\b', observation, re.I):
            from .authentication_methods import authentication_method_report
            registration = authentication_method_report(client)
            if registration['available'] and registration['metrics'].get('method_inventory_known'):
                count = registration['metrics'].get('passwordless_registered', 0)
                row['Observation'] = (f"{count} of {registration['total_users']} returned users have a recognized passwordless method registered. "
                    'This is registration evidence, not actual sign-in use or enforcement. Authenticator passwordless phone sign-in is not phishing-resistant.')
                row.update(EvidenceSource='auth_methods', EvidenceComplete=registration['complete'],
                           EvidenceScope='Users returned by the authentication registration report')
                population = registration['total_users']
                state = (getattr(client, 'collection_status', {}) or {}).get('auth_methods', {}) or {}
                row['InvestigationEvidence'] = {
                    'kind': 'planning',
                    'reason': (f'{count} of {population} returned users have a recognized passwordless method registered. '
                        f'The remaining {population - count} users are not established as affected or suitable rollout targets by this aggregate. '
                        'The rollout population, exceptions and intended authentication strength require a planning decision. '
                        'Actual passwordless sign-in use was not collected for this registration finding; registration alone does not prove use or enforcement.'),
                    'source': {
                        'api': 'https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails',
                        'scope': 'Returned registration-report population, including members and guests; administrators overlap those populations',
                        'population': population, 'recognized_passwordless_registered': count,
                        'method_inventory_known': registration['metrics'].get('method_inventory_known'),
                        'filter': 'Recognized registered passwordless methods; no affected-user selection is inferred from the complement',
                        'window': 'Registration report snapshot; report update dates are not sign-in dates',
                        'report_updated_from': registration.get('updated_from') or 'Not retained',
                        'report_updated_to': registration.get('updated_to') or 'Not retained',
                        'complete': registration['complete'], 'availability': registration['source_state'],
                        'pages': state.get('pages_collected', 'Not retained'), 'truncated': state.get('truncated', 'Not retained'),
                        'limitations': 'Multiple registered methods may overlap; unknown or conflicting method inventories do not prove absent registration. Pilot eligibility, permissions, licensing and retention were not independently validated by this planning observation.',
                    },
                }
            else:
                row['Observation'] = 'The saved passwordless summary cannot be validated from raw registration methods. Actual usage and phishing resistance remain unconfirmed.'
                row.update(EvidenceSource='auth_methods', EvidenceComplete=False)
                row['InvestigationEvidence'] = {'kind': 'unavailable',
                    'reason': 'The saved passwordless summary cannot be reproduced because retained registration method inventories are missing or unknown. No affected rollout-user population or actual passwordless sign-in use can be established.',
                    'source': {'api': 'https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails',
                               'scope': 'Retained registration-report records', 'availability': registration['source_state']}}
            if row.get('Disposition') == 'Assurance' or row.get('Status') == 'Success':
                row.update(Disposition='Reference', Status='Insight', Recommendation='')
        if (service == "Entra" and re.search(r"\d+ requiring MFA|blocking legacy authentication", observation)
                and source_is_complete(client, "ca_policies")):
            from .tenant_baseline import conditional_access_facts, enforced_mfa_policy_count
            policies = getattr(client, "ca_policies", []) or []
            states = [str((item.get("state") if isinstance(item, dict) else getattr(item, "state", "")) or "").lower() for item in policies]
            facts = conditional_access_facts(policies)
            row["Observation"] = (f"{len(policies)} Conditional Access policies: {states.count('enabled')} enforced, "
                                  f"{sum(state.startswith('enabledfor') for state in states)} report-only, {states.count('disabled')} disabled. "
                                  f"{enforced_mfa_policy_count(policies)} enforced polic{'y requires' if enforced_mfa_policy_count(policies) == 1 else 'ies require'} MFA; "
                                  f"{len(facts['legacy_block_all'])} block{'s' if len(facts['legacy_block_all']) == 1 else ''} legacy authentication for all users. "
                                  "The tenant-wide sign-in check states whether all users are covered.")
            if row.get("Disposition") == "Assurance" or row.get("Status") == "Success":
                row.update(Disposition="Reference", Status="Insight", Recommendation="")
        if row.get("Feature") == "Microsoft 365 data governance evidence" and row.get("Disposition") == "Coverage":
            # Collection summary for the engineer register; each required check
            # carries its own gap, so this is not a second action (3.0).
            row.update(Disposition="Reference", EvidenceBasis="Collection context", DomainId="data_protection")
        legacy_count = re.search(r"\b([0-9]+) legacy authentication sign-ins?\b", observation, re.I)
        if service == "Entra" and legacy_count and int(legacy_count.group(1)) > 0:
            # Older saved recommendations had no event-detail route and claimed
            # a 30-day window or successful bypass from a client-type count.
            count = int(legacy_count.group(1))
            row.setdefault("OriginalObservation", observation)
            row.update(FindingKey="entra.signins.legacy_auth", EvidenceKey="legacy_signin_detail",
                       EvidenceSource="entra_signin_logs", EvidenceComplete=source_is_complete(client, "signin_logs"),
                       EvidenceScope="Returned sign-in log records classified by clientAppUsed; includes successful and failed attempts",
                       ReportedLegacySignInCount=count,
                       Observation=f"{count} legacy authentication sign-in attempt{'s' if count != 1 else ''} detected in the returned sign-in records. Review the event outcomes before deciding whether access succeeded.",
                       Recommendation="Review the linked sign-in records to identify the accounts, applications, clients and IP addresses involved. Distinguish successful requests from failed or blocked attempts using the error code and Conditional Access result. Confirm business dependencies, migrate required clients to modern authentication, and test a policy to block legacy authentication before enforcement.")
        if service == "Entra" and "No legacy authentication sign-ins" in observation:
            complete = source_is_complete(client, "signin_logs")
            row.update(EvidenceSource="signin_logs", EvidenceComplete=complete, EvidenceScope="Returned sign-in log records",
                       Observation="No legacy authentication sign-ins were found in the returned sign-in records. This sample does not establish that all access uses modern authentication or that every security control is effective."
                       if complete else "The sign-in query did not complete. The absence of legacy authentication has not been established.")
            if not complete:
                row.update(Disposition="Coverage", Status="Not Assessed", SourceStatus="Not Assessed", EvidenceAvailable="No", EvidenceBasis="Not verified",
                           Recommendation="Review the dated sign-in evidence and confirm the scope and result of legacy authentication checks.")
        if (service == "Entra" and re.search(r"\b\d+ of \d+ users\b", observation, re.I)
                and "mfa" in observation.lower() and ("enroll" in observation.lower() or "registered" in observation.lower())
                and (row.get("Disposition") == "Action" or str(row.get("Status") or "").lower() in {"action required", "attention required", "warning", "critical"})):
            row.update(FindingKey="entra.authentication.mfa_registration",
                       EvidenceKey="authentication_detail;mfa_registration_detail",
                       EvidenceSource="entra_auth_methods", EvidenceComplete=source_is_complete(client, "auth_methods"))
        if re.search(r"\bis active in\b", observation, re.I) or (
                service == "Defender" and row.get("Feature") in {"Microsoft Defender XDR", "Microsoft Defender for Endpoint", "Microsoft Defender for Office 365 (Plan 2)"}):
            row.update(Observation=f"The {row.get('Feature')} service plan was present in the collected license inventory. Operational protection requires separate control evidence.",
                       Recommendation="", Disposition="Reference", EvidenceBasis="License signal", EvidenceKey="service_plan_inventory", Confidence="Low")
            qualified.append(row)
            continue
        if service == "Purview":
            source = next((key for markers, key in (
                (("ediscovery",), "ediscovery_cases"),
                (("communication compliance", "communication_compliance"), "comm_compliance"),
                (("insider risk", "insider_risk"), "insider_risk"),
                (("information barrier", "information_barrier"), "information_barriers"),
                (("label publication", "label publishing", "label_policies"), "label_policies"),
                (("sensitivity label", "sensitivity_labels", "label deployment"), "sensitivity_labels"),
                (("retention label", "retention_labels", "retention strategy"), "retention_labels"),
                (("audit status", "audit.state", "auditing - configuration"), "audit_config"),
                (("rms.state", "rights management - configuration"), "irm_config"),
                (("customer_lockbox.state",), "org_config"),
                (("dlp", "data loss prevention"), "dlp_policies"),
            ) if any(marker in text for marker in markers)), None)
            if source:
                payload = getattr(client, source, {}) or {} if client else {}
                complete = source_is_complete(client, source, payload)
                row.update(EvidenceSource="purview_" + SOURCE_ALIASES.get(source, source), EvidenceComplete=complete)
                state = (getattr(client, "collection_status", {}) or {}).get(SOURCE_ALIASES.get(source, source), {}) if client else {}
                state = state if isinstance(state, dict) else {}
                if source == 'org_config' and 'customer_lockbox.state' in text:
                    row['InvestigationEvidence'] = _lockbox_investigation(client, payload, state)
                    if row['InvestigationEvidence']['kind'] == 'unavailable':
                        row.setdefault('OriginalObservation', observation)
                        row.update(Observation=row['InvestigationEvidence']['reason'], EvidenceComplete=False,
                                   EvidenceBasis='Not verified', Confidence='Unknown',
                                   Recommendation='Confirm the original Customer Lockbox setting in a dated organization-configuration export or the admin center, then evaluate the contractual and support-access requirements.')
                    else:
                        setting = row['InvestigationEvidence']['records'][0]
                        row.setdefault('OriginalObservation', observation)
                        row['Observation'] = (f"The retained organization configuration reports Customer Lockbox as {'enabled' if setting['Enabled'] else 'disabled'} "
                            f"({setting['Source Field']}={setting['Original Value']}). The setting alone does not establish contractual need or approval-workflow effectiveness.")
                if not complete and source == "sensitivity_labels" and state.get("evidence_quality") == "preview" and state.get("available"):
                    # Graph beta label definitions: observed, but publishing and
                    # enforcement are not established, so the question stays open.
                    count = payload.get("total_labels")
                    active = state.get("active_count")
                    active_note = f" ({active} active)" if isinstance(active, int) else ""
                    row.update(Observation=f"Microsoft Graph (beta) returned {count if isinstance(count, int) else 'an unknown number of'} sensitivity label definition(s){active_note}. "
                                           "Label publishing policies, default labels and mandatory labeling were not collected, so label deployment to users is not established.",
                               Recommendation="Confirm label publishing policies in the Microsoft Purview portal, or enable Purview PowerShell collection (application token with a read-only role, certificate, or administrator sign-in) and rerun.",
                               Disposition="Coverage", Status="Not Assessed", SourceStatus="Not Assessed",
                               EvidenceAvailable="Partial", EvidenceBasis="Preview API", Confidence="Medium",
                               SourceAvailability="partial")
                elif not complete:
                    availability = source_availability(client, source, payload)
                    optional = source in {"ediscovery_cases", "comm_compliance", "insider_risk", "information_barriers"}
                    unlock = state.get("unlock") or ""
                    row.update(Observation=f"{source.replace('_', ' ').capitalize()} evidence was {availability.replace('_', ' ')} at collection time. Its configuration and object count were not established.",
                               Recommendation="" if optional else ("Obtain a dated configuration review and record its scope and result."
                                                                   + (f" To collect it automatically: {unlock}" if unlock else "")),
                               Disposition="Reference" if optional else "Coverage", Status="Not Assessed", SourceStatus="Not Assessed",
                               EvidenceAvailable="No", EvidenceBasis="Not verified", Confidence="Unknown", SourceAvailability=availability)
                elif source == "retention_labels" and "retention_policies" in (getattr(client, "collection_status", {}) or {}):
                    row["Observation"] = re.sub("retention labels", "retention policies", observation, flags=re.I)
                elif source == "sensitivity_labels":
                    label_count = payload.get("total_labels")
                    if isinstance(label_count, (int, float)) and label_count > 0:
                        row["Observation"] = f"Purview returned {label_count:g} sensitivity label definitions. Publishing scope, automatic labeling and effective protection require separate evidence."
        if service == "Defender" and "device onboarding" in str(row.get("Feature") or "").lower():
            complete = source_is_complete(client, "machines")
            devices = getattr(client, "defender_devices", []) or [] if client else []
            if complete and devices:
                row.update(Observation=f"Defender returned {len(devices)} device record(s). The expected pilot device population and browser protection baseline were not supplied; inventory count alone does not establish coverage.",
                           Recommendation="", Disposition="Reference", EvidenceBasis="Inventory observation",
                           EvidenceSource="defender_machines", EvidenceScope="Devices returned by the Defender machines query", EvidenceComplete=True)
        qualified.append(row)
    return qualified


def assess_defender_configuration(client, collected_at=None):
    """Evaluate incident facts independently of alert, device and license feeds."""
    if client is None:
        return []
    complete = source_is_complete(client, "incidents")
    incidents = getattr(client, "security_incidents", []) or []
    active = [row for row in incidents if str(row.get("status") or "").lower() in {"active", "new", "inprogress", "in_progress"}]
    high = sum(str(row.get("severity") or "").lower() == "high" for row in active)
    if active:
        observation = f"The incident query returned {len(active)} active incident(s), including {high} with high severity."
        disposition, status = "Action", "Action Required"
        action = "Review and resolve the active incidents, starting with high severity, or record the security owner's approved treatment."
    elif complete:
        observation = "No active incidents were returned by the completed Microsoft Graph Security incident query. This observation covers the incidents visible to that service at collection time."
        disposition, status, action = "Assurance", "Success", ""
    else:
        observation = "The security incident query did not complete. The presence and severity of active incidents remain unverified; alert or device results do not establish incident status."
        disposition, status = "Coverage", "Not Assessed"
        action = "Review active incidents in the Microsoft Defender portal and record the result and date."
    row = new_recommendation("Defender", "Review active security incidents", observation, action,
        status=status, disposition=disposition, priority="High" if high else "Medium",
        finding_key="defender.incidents.current", evidence_key="defender_incident_detail",
        evidence_basis="Tenant evidence" if complete or active else "Not verified", confidence="High" if complete else "Unknown")
    row.update(ControlId="THREAT.INCIDENTS", EvidenceSource="defender_incidents", EvidenceComplete=complete,
               EvidenceScope="Incidents visible to Microsoft Graph Security for the assessed tenant", ObservationDate=collected_at or "",
               SourceAvailability=source_availability(client, "incidents"))
    records = [row]
    devices = getattr(client, "defender_devices", []) or []
    high_risk = [device for device in devices if str(device.get("riskScore") or "").lower() == "high"]
    if high_risk:
        device_row = new_recommendation("Defender", "Review high-risk devices",
            f"The Defender device query returned {len(high_risk)} device(s) with high risk. Confirm whether these devices are used by pilot participants.",
            "Investigate the returned high-risk devices and record remediation or an approved treatment before they are used for the pilot.",
            status="Action Required", disposition="Action", priority="High", finding_key="defender.devices.high_risk",
            evidence_key="defender_device_detail", evidence_basis="Tenant evidence", confidence="High")
        device_row.update(ControlId="ENDPOINT.POSTURE", EvidenceSource="defender_machines", EvidenceComplete=source_is_complete(client, "machines"),
                          EvidenceScope="Devices returned by the Defender machines query", ObservationDate=collected_at or "")
        records.append(device_row)
    return records


def assess_purview_configuration(client, collected_at=None):
    """Evaluate available policy facts without inferring licenses or enforcement.

    An empty, successfully collected policy list is a measurement. A missing
    list is unknown and leaves the required question open in the shared model.
    """
    if client is None:
        return []
    provenance = getattr(client, 'cache_provenance', {}) or {}
    date = provenance.get('collected_at') or collected_at or ''
    records = []
    def finding(key, feature, observation, action, priority='Medium'):
        row = new_recommendation('Purview', feature, observation, action,
            priority=priority, status='Attention Required', disposition='Action',
            finding_key=key, evidence_key='purview_policy_detail',
            evidence_basis='Tenant evidence', confidence='High')
        row.update(SourceType=provenance.get('source_type') or 'tenant_collection',
            SourceFile=provenance.get('source_file', ''), ObservationDate=date,
            EvidenceScope='Tenant policy configuration', EvidenceComplete=True)
        records.append(row)

    for attribute, list_key, feature, action in (
        ('sensitivity_labels', 'labels', 'Define sensitivity labels for business content',
         'Agree the classifications needed for pilot content and publish the applicable labels to the pilot users.'),
        ('label_policies', 'policies', 'Publish sensitivity labels to the intended users',
         'Confirm the labels and user populations, then define the required publishing policies.'),
        ('dlp_policies', 'policies', 'Define data loss prevention policies for the pilot content',
         'Agree the sensitive information and permitted sharing scenarios, then configure and test data loss prevention policies for that scope.'),
    ):
        data = getattr(client, attribute, {}) or {}
        if source_is_complete(client, attribute, data) and list_key in data and data[list_key] == []:
            finding('purview.raw.' + attribute, feature,
                'The collected configuration contains zero ' + attribute.replace('_', ' ') + '. This is a configuration observation; content sensitivity and effective access require separate checks.', action)
    audit = getattr(client, 'audit_config', {}) or {}
    if source_is_complete(client, 'audit_config', audit) and audit.get('unified_audit_enabled') is False:
        finding('purview.raw.audit_disabled', 'Enable the audit trail needed for investigation',
            'Unified audit log ingestion was disabled in the collected configuration.',
            'Ask the compliance administrator to confirm the required audit coverage and enable ingestion before the pilot.', 'High')
    policies = getattr(client, 'dlp_policies', {}) or {}
    rows = policies.get('policies') or []
    if isinstance(rows, dict):
        rows = [rows]
    modes = [str(r.get('Mode') or r.get('mode') or '').lower() for r in rows if isinstance(r, dict)]
    if source_is_complete(client, 'dlp_policies', policies) and modes and all(mode in {'disable', 'disabled', 'testwithnotifications', 'testwithoutnotifications'} for mode in modes):
        finding('purview.raw.dlp_mode', 'Confirm enforcement of data loss prevention policies',
            'All returned data loss prevention policies are disabled or in simulation mode. Their configured rules do not establish enforced protection.',
            'Review policy coverage and simulation results, then approve enforcement for the intended pilot content.')
    return records

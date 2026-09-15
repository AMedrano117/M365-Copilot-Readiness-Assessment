"""Deterministic checks of raw control evidence, shared by collection and replay."""

from .new_recommendation import new_recommendation
from .source_evidence import source_is_complete, source_availability, SOURCE_ALIASES
import re


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
        text = " ".join(str(row.get(k) or "") for k in ("Feature", "FindingKey", "Observation")).lower()
        observation = str(row.get("Observation") or "")
        if service == "Entra" and "No legacy authentication sign-ins" in observation:
            complete = source_is_complete(client, "signin_logs")
            row.update(EvidenceSource="signin_logs", EvidenceComplete=complete, EvidenceScope="Returned sign-in log records",
                       Observation="No legacy authentication sign-ins were found in the returned sign-in records. This sample does not establish that all access uses modern authentication or that every security control is effective."
                       if complete else "The sign-in query did not complete. The absence of legacy authentication has not been established.")
            if not complete:
                row.update(Disposition="Coverage", Status="Not Assessed", SourceStatus="Not Assessed", EvidenceAvailable="No", EvidenceBasis="Not verified",
                           Recommendation="Review the dated sign-in evidence and confirm the scope and result of legacy authentication checks.")
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
                if not complete:
                    availability = source_availability(client, source, payload)
                    optional = source in {"ediscovery_cases", "comm_compliance", "insider_risk", "information_barriers"}
                    row.update(Observation=f"{source.replace('_', ' ').capitalize()} evidence was {availability.replace('_', ' ')} at collection time. Its configuration and object count were not established.",
                               Recommendation="" if optional else "Obtain a dated configuration review and record its scope and result.",
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
        observation = f"The incident query returned {len(active)} active incident(s), including {high} with high severity. Review their impact on the intended pilot population."
        disposition, status = "Action", "Action Required"
        action = "Review and resolve active incidents affecting pilot accounts or devices, or record an approved treatment."
    elif complete:
        observation = "No active incidents were returned by the completed Microsoft Graph Security incident query. This observation covers the incidents visible to that service at collection time."
        disposition, status, action = "Assurance", "Success", ""
    else:
        observation = "The security incident query did not complete. The presence and severity of active incidents remain unverified; alert or device results do not establish incident status."
        disposition, status = "Coverage", "Not Assessed"
        action = "Obtain a dated review of active incidents affecting the pilot population and record its scope and outcome."
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

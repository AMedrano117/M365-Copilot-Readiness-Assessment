"""License inventory provenance is supporting context, never entitlement proof."""


def normalize_licensing(row, bundle, associated, dependencies, plan_names, condition):
    bundle = bundle or {}
    sheets = bundle.get('sheets') or {}
    inventory = (sheets.get('service_plan_inventory') or {}).get('rows') or []
    sources = bundle.get('source_statuses') or {}
    envelopes = [value for key, value in sources.items() if key in {'license_details', 'licenses', 'service_plan_inventory', 'entra_license_details'} and isinstance(value, dict)]
    for key in ('license_details', 'licenses', 'service_plan_inventory'):
        envelopes.extend(entry.get('source') or {} for entry in (bundle.get('assessment_sources') or {}).get(key, []))
    names = set().union(*(plan_names.get(label, set()) for label in associated))
    for label, plans in plan_names.items():
        if any(label in value for value in dependencies): names.update(plans)
    matched = [item for item in inventory if item.get('Service Plan Name') in names]
    statuses = {str(item.get('Provisioning Status') or '').lower() for item in matched}
    actual = sorted({item['Service Plan Name'] for item in matched})
    active = any(str(item.get('Provisioning Status') or '').lower() == 'success' for item in matched)
    complete = bool(envelopes) and all(e.get('complete') is True and not e.get('truncated') and
        e.get('availability_status', e.get('status')) in {'available','empty'} for e in envelopes)
    partial = any(e.get('complete') is False or e.get('truncated') or e.get('availability_status') == 'partial' for e in envelopes)
    if not names:
        state = 'NeedsScenarioConfirmation' if dependencies else 'NotApplicable'
    elif statuses & {'unknown', ''}:
        state = 'Conflicting' if active else 'Incomplete'
    elif partial:
        state = 'Incomplete'
    elif actual:
        state = 'Observed'
    elif complete:
        state = 'NotObserved'
    elif inventory or envelopes:
        state = 'Incomplete'
    else:
        state = 'NotCollected'
    verification = 'assignment_observed' if row.get('LicenseAssignmentObserved') is True else 'license_plan_observed' if actual else 'scenario_needs_confirmation' if dependencies else 'not_established'
    return {'AssociatedServicePlans':actual, 'LicenseInventoryEvidenceState':state,
            'AssociatedServicePlanStates':[dict(item) for item in matched],
            'LicenseSource':'retained service-plan inventory' if matched else 'retained collection envelope' if envelopes else 'not collected',
            'EntitlementVerificationState':verification,
            'LicenseContextQualification':'Original associated feature is compatibility context. Observed service plans and licensing dependencies do not establish assignment, effective controls or scenario entitlement.'}


def licensing_columns(row):
    """Shared additive workbook vocabulary; legacy state remains compatible."""
    return {'Associated Service Plan':'; '.join(row.get('AssociatedServicePlans') or []),
            'License Inventory Evidence State':row.get('LicenseInventoryEvidenceState'),
            'License Source':row.get('LicenseSource'),
            'Entitlement Verification State':row.get('EntitlementVerificationState')}

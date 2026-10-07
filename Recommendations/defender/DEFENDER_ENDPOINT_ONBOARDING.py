"""Describe retained onboarding evidence; estate coverage is reconciled separately."""
from Core.new_recommendation import new_recommendation


async def get_recommendation(client, defender_client=None, services_and_licenses=None, purview_client=None):
    state = (getattr(defender_client, 'collection_status', {}) or {}).get('machines', {})
    readable = defender_client is not None and (getattr(defender_client, 'data_sources', {}) or {}).get('machines', getattr(defender_client, 'defender_api_available', False))
    records = getattr(defender_client, 'defender_devices', []) or []
    if not readable:
        return new_recommendation('Defender', 'Defender for Endpoint - Device Onboarding',
            'The Defender machines inventory could not be read completely; onboarding and health are unknown.',
            'Verify provisioning and Machine.Read.All access, then collect again. A failed request does not mean zero onboarded devices.',
            status='Not Assessed', priority='Medium', disposition='Coverage', evidence_key='defender_device_detail',
            investigation={'kind':'unavailable','reason':state.get('reason') or 'The machines query was unavailable or incomplete.','source':state})
    if not records:
        return new_recommendation('Defender','Defender for Endpoint - Device Onboarding',
            'The returned inventory contains no retained device rows. This does not establish onboarding coverage of the active estate.',
            'Reconcile the active device inventory and investigate missing device records before calculating coverage.',
            status='Not Assessed',priority='Medium',disposition='Coverage',evidence_key='defender_device_detail',
            investigation={'kind':'unavailable','reason':'Individual machine records are empty or were not retained; a summary count cannot establish onboarding or reporting.','source':state})
    selected=[row for row in records if str(row.get('onboardingStatus',row.get('onboardingstatus',''))).lower()=='onboarded']
    unknown=sum(not row.get('onboardingStatus',row.get('onboardingstatus')) for row in records)
    rec=new_recommendation('Defender','Defender for Endpoint - Device Onboarding',
        f'{len(selected)} of {len(records)} retained machine records explicitly report onboarded; {unknown} have unknown onboarding state. Inventory membership does not prove recent endpoint reporting or full estate coverage.',
        'Review the Device Reconciliation and Device Coverage worksheets for active, stale, duplicate, unmatched and excluded records.',
        status='Insight',priority='Low',disposition='Reference',evidence_key='defender_device_detail',
        investigation={'kind':'records','records':records,'sheet_name':'Onboarding Source Records','record_id_field':'id',
            'source':state,'reason':'Complete retained machine inventory; the explicitly onboarded subset is identified by onboardingStatus.'})
    rec.update(EvidenceLevel='configuration',EvidenceSource='defender_machines',EvidenceComplete=state.get('complete',False),EvidenceScope='Returned Defender machine inventory')
    return rec

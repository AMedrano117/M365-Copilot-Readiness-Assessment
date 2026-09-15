"""Offline entry point. This module deliberately does not import live orchestration."""

from .offline_collection import collection_context, empty_service_results, load_collection, refresh_saved_freshness
from uuid import UUID
from pathlib import Path


def run_offline_report(args):
    from .processor import process_and_print_all_information

    if not (args.collection_input or args.sam_report or args.dspm_report or args.reports_dir
            or args.copilot_readiness_export or args.copilot_dashboard_export or args.power_platform_inventory
            or args.prior_report or args.purview_cache or getattr(args, 'portal_review', None)):
        raise ValueError("Provide a saved collection, --prior-report, --purview-cache, --portal-review, or at least one portal export for offline mode.")
    payload = load_collection(args.collection_input) if args.collection_input else None
    from .assessment_package import restore_arguments, record_package_run, save_rebuild_recipe, render_with_failure_receipt, new_offline_package
    args = restore_arguments(args, payload)
    if args.snapshot_json:
        target = Path(args.snapshot_json).resolve()
        protected = {Path(args.collection_input).resolve()} if args.collection_input else set()
        from .assessment_package import INPUT_KEYS
        for role in INPUT_KEYS:
            supplied = getattr(args, role, None)
            if not supplied:
                continue
            for value in supplied if isinstance(supplied, (list, tuple)) else [supplied]:
                original = Path(value).resolve()
                protected.add(original)
                if original.is_dir() and target.is_relative_to(original):
                    protected.add(target)
        if getattr(args, 'portal_review', None) and Path(args.portal_review).is_file():
            from .portal_review import load_portal_review
            protected.update(Path(path) for path in load_portal_review(args.portal_review, embed_assets=False)['asset_paths'])
        if payload and payload.get('package_directory'):
            package_folder = Path(payload['package_directory'])
            protected.update(package_folder / name for name in ('collection.json', 'rebuild.json', 'operator-log.jsonl'))
            if payload.get('source_file'):
                protected.add((package_folder.parent / payload['source_file']).resolve())
            protected.update(Path(path).resolve() for paths in payload.get('resolved_inputs', {}).values() for path in paths)
            if target.is_relative_to(package_folder / 'inputs') or target.is_relative_to(package_folder / 'rebuilds'):
                protected.add(target)
        if target in protected:
            raise ValueError('Snapshot output cannot overwrite a collection, original input, or package manifest. Choose a new output path.')
    results = payload["service_results"] if payload else empty_service_results()
    prior = None
    if args.prior_report:
        from .prior_report_import import load_prior_report
        prior = load_prior_report(args.prior_report, include_user_details=args.include_user_usage_detail)
    cache = None
    if args.purview_cache:
        from .purview_cache_import import load_purview_cache
        cache = load_purview_cache(args.purview_cache)
        results['purview_info'] = cache['service_info']
    # Compare only actual GUID identifiers; a display name is not a tenant ID.
    tenant_guids = set()
    candidates = [(payload or {}).get('tenant_id'), (cache or {}).get('tenant_id'),
                  (prior or {}).get('tenant_id'), args.tenant_id]
    for candidate in candidates:
        try:
            tenant_guids.add(str(UUID(str(candidate))))
        except (ValueError, AttributeError, TypeError):
            continue
    if len(tenant_guids) > 1:
        raise ValueError('The saved evidence identifies different tenants. Use one tenant per offline build.')
    expected_tenant_id = next(iter(tenant_guids), None) or next((value for value in candidates if value), None)
    from .pdf_report_import import prepare_pdf_review
    args.portal_review = prepare_pdf_review(args.reports_dir, getattr(args, 'portal_review', None),
                                            tenant_id=expected_tenant_id, evaluation_date=args.evaluation_date)
    if not args.include_user_usage_detail:
        client = results['m365_result'][0].get('_client')
        for name, field in (('copilot_usage', 'user_detail'), ('copilot_readiness_export', 'user_details')):
            evidence = getattr(client, name, None)
            if isinstance(evidence, dict):
                evidence.pop(field, None)
    refresh_saved_freshness(results, args.evaluation_date)
    context = collection_context(payload, args.collection_input, args.evaluation_date)
    migration = context.get('methodology_migration')
    if migration:
        from .console_reporting import print_paragraph
        print_paragraph(
            f"Methodology migration {migration['from']} -> {migration['to']}: {migration['reason']}",
            tone='warning')
    from .data_exposure_assessment import _positive_int_env
    settings = dict((payload or {}).get('assessment_settings', {}))
    for key, environment, default in (
        ('provider_evidence_max_age_days', 'PROVIDER_EVIDENCE_MAX_AGE_DAYS', 90),
        ('sam_report_max_age_days', 'SAM_REPORT_MAX_AGE_DAYS', 35),
        ('dspm_report_max_age_days', 'DSPM_REPORT_MAX_AGE_DAYS', 8),
    ):
        settings.setdefault(key, _positive_int_env(environment, default))
    from .lifecycle_report_settings import lifecycle_settings
    settings = lifecycle_settings(settings, max_age_days=getattr(args, 'lifecycle_report_max_age_days', None),
                                  report_dates=getattr(args, 'lifecycle_report_date', None),
                                  evaluation_date=args.evaluation_date)
    context['assessment_settings'] = settings
    historical_sources = []
    if prior:
        historical_sources.append({'source_file': prior['source_file'], 'source_type': 'Prior assessment',
                                   'reported_at': prior.get('generated_at', '')})
    if cache:
        historical_sources.append({'source_file': cache['source_file'], 'source_type': 'Purview configuration cache',
                                   'reported_at': cache['collected_at']})
    if historical_sources:
        context['historical_sources'] = historical_sources
        context['scope'] = ('Saved tenant collection; ' if payload and payload.get('has_tenant_collection', True) else '') + '; '.join(
            [item['source_type'] for item in historical_sources] + ['Supplied portal exports'])
    tenant_name = (payload or {}).get("tenant_name") or args.tenant_name or (prior or {}).get('tenant_name') or "Portal export review"
    receipt = dict(
        folder=(payload or {}).get('package_directory'), mode='offline', tenant_id=expected_tenant_id,
        collected_at=context.get('collected_at'), evaluation_date=args.evaluation_date,
        diagnostics=(payload or {}).get('package', {}).get('diagnostics', []),
        collection_input=args.collection_input,
        methodology_migration=migration,
    )
    result = render_with_failure_receipt(
        process_and_print_all_information, receipt=receipt,
        **results, tenant_name=tenant_name, open_html_report=args.open_html_report,
        report_format=args.report_format, sam_report_paths=args.sam_report,
        dspm_report_paths=args.dspm_report,
        data_exposure_enabled=(payload or {}).get('assessment_settings', {}).get('data_exposure_enabled', True),
        assessment_profile=args.assessment_profile, provider_evidence=args.provider_evidence,
        snapshot_json=args.snapshot_json, baseline=args.baseline,
        enabled_collectors=(payload or {}).get("enabled_collectors", []),
        connection_results=(payload or {}).get("connection_results", []),
        reports_dirs=args.reports_dir, copilot_readiness_export=args.copilot_readiness_export,
        collection_context=context, expected_tenant_id=expected_tenant_id, prior_report=prior,
        offline_copilot_dashboard_export=args.copilot_dashboard_export,
        offline_power_platform_inventory=args.power_platform_inventory,
        include_user_usage_detail=args.include_user_usage_detail,
        portal_review=getattr(args, 'portal_review', None),
    )
    if isinstance(result, dict):
        expected_tenant_id = expected_tenant_id or (result.get('evidence_bundle') or {}).get('expected_tenant_id')
        receipt['tenant_id'] = expected_tenant_id
    if isinstance(result, dict) and args.snapshot_json:
        result['snapshot_path'] = args.snapshot_json
    recipe = None
    recovered_raw_collection = False
    if result and result.get('html_path'):
        if not receipt['folder']:
            receipt['folder'] = new_offline_package(tenant_name)
            if payload and payload.get('format') == 'm365-readiness-collection':
                # Recover a raw collection saved before supplemental packaging failed.
                # Copy the immutable original; do not synthesize or overwrite a collection.
                import shutil
                shutil.copy2(args.collection_input, Path(receipt['folder']) / 'collection.json')
                recovered_raw_collection = True
        recipe = save_rebuild_recipe(receipt['folder'], args,
                                     settings,
                                     tenant_id=expected_tenant_id, tenant_name=tenant_name,
                                     methodology_migration=migration)
        if recovered_raw_collection:
            receipt['collection_input'] = str(Path(receipt['folder']) / 'rebuild.json')
    record_package_run(
        **receipt,
        outputs=result, inputs=(recipe or (payload or {}).get('package', {})).get('files', []),
        status='complete' if result and result.get('html_path') else 'incomplete',
    )
    return 0 if result and result.get("html_path") else 1

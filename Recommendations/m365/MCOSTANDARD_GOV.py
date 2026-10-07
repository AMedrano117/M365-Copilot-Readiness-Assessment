"""Qualified legacy service-plan reference; licensing does not establish use."""

from Core.guidance_verification import legacy_service_reference


def get_recommendation(sku_name, status="Success", m365_insights=None):
    return legacy_service_reference(sku_name, status, 'Skype for Business Online (Plan 2, government)', 'skype_online')

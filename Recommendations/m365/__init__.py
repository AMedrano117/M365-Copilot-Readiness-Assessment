"""
__init__.py for m365 recommendations
Dynamically imports all recommendation modules
"""
import os
import importlib
import sys
from pathlib import Path
from Core.spinner import get_timestamp, _stdout_lock

# Get all .py files in this directory except __init__.py and helper modules
current_dir = Path(__file__).parent
recommendation_modules = {}

# Helper modules that don't contain get_recommendation function
helper_modules = {'m365_insights'}

for file_path in current_dir.glob("*.py"):
    if file_path.name != "__init__.py" and file_path.stem not in helper_modules:
        module_name = file_path.stem
        module = importlib.import_module(f"Recommendations.m365.{module_name}")
        # Store with uppercase key for case-insensitive lookup
        recommendation_modules[module_name.upper()] = module.get_recommendation

# Update progress bar
from Core.module_loader import get_progress_tracker
get_progress_tracker().update('M365', len(recommendation_modules))

def get_feature_recommendation(feature_name, sku_name, status="Success", client=None, m365_insights=None):
    """
    Get recommendation for a specific feature
    
    Args:
        feature_name: Technical feature/service plan name (e.g., 'GRAPH_CONNECTORS_COPILOT')
        sku_name: SKU name where the feature is found
        status: Provisioning status of the feature
        client: Optional Graph client for deployment checks
        m365_insights: Optional pre-computed M365 usage metrics
    
    Returns:
        dict: Recommendation object
    """
    # Use uppercase for case-insensitive lookup
    if feature_name.upper() in recommendation_modules:
        func = recommendation_modules[feature_name.upper()]
        # Check if function supports parameters
        import inspect
        sig = inspect.signature(func)
        has_client_param = 'client' in sig.parameters
        has_insights_param = 'm365_insights' in sig.parameters
        
        # Handle async functions
        if inspect.iscoroutinefunction(func):
            import asyncio
            
            # Prepare coroutine with appropriate parameters
            if has_insights_param:
                coro = func(sku_name, status, client=client, m365_insights=m365_insights)
            elif has_client_param:
                coro = func(sku_name, status, client=client)
            else:
                coro = func(sku_name, status)
            
            # Check if we're in a running event loop
            try:
                asyncio.get_running_loop()
                # We're in an async context - return coroutine for caller to await
                return coro
            except RuntimeError:
                # No running loop - safe to use asyncio.run()
                result = asyncio.run(coro)
        else:
            # Handle sync functions with appropriate parameters
            if has_insights_param:
                result = func(sku_name, status, m365_insights=m365_insights)
            elif has_client_param:
                result = func(sku_name, status, client=client)
            else:
                result = func(sku_name, status)
        
        # Handle functions that return lists of recommendations
        if isinstance(result, list):
            return result
        return result
    
    # No dedicated recommendation module exists for this service plan.
    #
    # These plans used to emit a boilerplate card each - "X is active in Y, providing
    # infrastructure and services that M365 Copilot depends on" for Success, and "Enable X to
    # provide collaboration, productivity, and knowledge management capabilities" for anything
    # else. Both are unfounded: with no module, the assessment has no Copilot-specific criteria
    # for the plan and cannot say whether it matters or whether enabling it would help. In a
    # typical tenant this produced ~50 filler cards that crowded out real findings, and the
    # "enable it" advice pointed at plans that were deliberately off or irrelevant.
    #
    # The plan is still recorded, with its provisioning status, in the Service Plan Inventory
    # workbook tab (see Core/evidence_layer.py), so the licensing picture stays complete.
    return None

"""
Power Virtual Agents - Copilot Studio & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

async def get_deployment_status(client, pp_insights=None):
    """Check agent session capacity planning infrastructure."""
    try:
        import asyncio
        
        result = {
            'available': True,
            'has_users': False,
            'user_count': 0,
            'has_teams': False,
            'team_count': 0,
            'environments': 0,
            'has_pp_access': False
        }
        
        tasks = [client.users.get(), client.groups.get()]
        
        # Use pre-computed Power Platform insights
        if pp_insights:
            result['has_pp_access'] = True
            result['environments'] = pp_insights.get('environments_total', 0)
            result['env_production'] = pp_insights.get('production_envs', 0)
            result['env_sandbox'] = pp_insights.get('sandbox_envs', 0)
            result['env_developer'] = 0  # Not in standard pp_insights
            result['connections_total'] = pp_insights.get('connections_total', 0)
            result['premium_connectors'] = pp_insights.get('premium_connectors', 0)
            result['custom_connectors'] = pp_insights.get('custom_connectors', 0)
        
        users, groups = await asyncio.gather(*tasks, return_exceptions=True)
        
        if not isinstance(users, Exception) and users and users.value:
            result['has_users'] = True
            result['user_count'] = len(users.value)
        
        if not isinstance(groups, Exception) and groups and groups.value:
            teams = [g for g in groups.value if hasattr(g, 'resource_provisioning_options') 
                    and g.resource_provisioning_options 
                    and 'Team' in g.resource_provisioning_options]
            result['team_count'] = len(teams)
            result['has_teams'] = len(teams) > 0
        
        return result
    except Exception as e:
        return {'available': False, 'error': str(e)}

async def get_recommendation(sku_name, status="Success", client=None, pp_client=None, pp_insights=None):
    """
    Virtual Agent User Session License - session capacity planning for agent concurrency.
    Returns 2 recommendations: license status + session capacity strategy based on PP infrastructure.
    """
    feature_name = "Power Virtual Agents"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    if status == "Success":
        license_rec = new_recommendation(
            service="Copilot Studio",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, enabling conversational AI agents through Copilot Studio",
            recommendation="",
            link_text="Microsoft 365 Documentation",
            link_url="https://learn.microsoft.com/microsoft-365/",
            status=status
        )
    else:
        license_rec = new_recommendation(
            service="Copilot Studio",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}, missing conversational AI agent creation platform",
            recommendation=f"Enable {feature_name} (now Copilot Studio) to build custom conversational agents for employee and customer scenarios.",
            link_text="Copilot Studio (formerly PVA)",
            link_url="https://learn.microsoft.com/microsoft-copilot-studio/",
            priority="Medium",
            status=status
        )
    
    if status == "Success" and client:
        deployment = await get_deployment_status(client, pp_insights)
        if deployment.get('available'):
            user_count = deployment.get('user_count', 0)
            team_count = deployment.get('team_count', 0)
            env_count = deployment.get('environments', 0)
            has_pp = deployment.get('has_pp_access', False)
            env_prod = deployment.get('env_production', 0)
            env_sandbox = deployment.get('env_sandbox', 0)
            env_dev = deployment.get('env_developer', 0)
            
            if has_pp and env_count > 0:
                # Build environment description
                env_desc = []
                if env_prod > 0:
                    env_desc.append(f"{env_prod} production")
                if env_sandbox > 0:
                    env_desc.append(f"{env_sandbox} sandbox")
                if env_dev > 0:
                    env_desc.append(f"{env_dev} developer")
                env_types = ", ".join(env_desc) if env_desc else ("1 environment" if env_count == 1 else f"{env_count} environments")
                
                # PP access - environment-based session capacity planning
                if user_count > 500 and team_count > 10:
                    deployment_rec = new_recommendation(
                        service="Copilot Studio",
                        feature=f"{feature_name} - High-Volume Session Planning",
                        observation=f"Large-scale infrastructure: {user_count} users, {team_count} Teams, {env_types}",
                        recommendation=f"Plan session capacity using measured pilot demand across {team_count} Teams and {env_types}: 1) record actual peak concurrent sessions and duration, 2) {'use dev/sandbox for load testing and production for approved live use' if env_prod > 0 else 'establish a governed test environment before enterprise scale'}, 3) monitor queue time and failures per environment, 4) define operational headroom from the observed peak and vendor limits, and 5) document the customer-owned scale or stop threshold. Do not estimate concurrency from an assumed percentage of users.",
                        link_text="Agent Session Management",
                        link_url="https://learn.microsoft.com/microsoft-copilot-studio/requirements-quotas",
                        priority="High",
                        status="Success"
                    )
                elif user_count > 100:
                    deployment_rec = new_recommendation(
                        service="Copilot Studio",
                        feature=f"{feature_name} - Session Capacity Planning",
                        observation=f"Medium-scale infrastructure: {user_count} users, {env_types}",
                        recommendation=f"Establish session capacity from a bounded pilot of the {user_count} potential users in {env_types}: 1) measure peak concurrency, duration, failures, and abandon rate, 2) {'use dev/sandbox for testing and production only after approval' if env_prod > 0 else 'establish a governed test environment'}, 3) monitor per-environment demand{' and compare environments' if env_count > 1 else ''}, 4) design overflow and human escalation, and 5) set capacity headroom and an expand/stop threshold from measured results rather than an assumed concurrent-use percentage.",
                        link_text="Session Capacity Planning",
                        link_url="https://learn.microsoft.com/microsoft-copilot-studio/analytics-sessions",
                        priority="Medium",
                        status="Success"
                    )
                else:
                    # Build observation with connector infrastructure
                    connections_total = deployment.get('connections_total', 0)
                    premium_conns = deployment.get('premium_connectors', 0)
                    custom_conns = deployment.get('custom_connectors', 0)
                    
                    obs_parts = [f"{user_count} users", env_types]
                    if connections_total > 0:
                        obs_parts.append(f"{connections_total} connection{'s' if connections_total != 1 else ''} available for agent actions")
                        if premium_conns > 0:
                            obs_parts.append(f"{premium_conns} premium connector{'s' if premium_conns != 1 else ''} (advanced agent capabilities)")
                        if custom_conns > 0:
                            obs_parts.append(f"{custom_conns} custom connector{'s' if custom_conns != 1 else ''} (org-specific integrations)")
                    else:
                        obs_parts.append("no connectors yet (opportunity: build connections to enterprise systems for agent actions)")
                    
                    deployment_rec = new_recommendation(
                        service="Copilot Studio",
                        feature=f"{feature_name} - Agent Session Strategy",
                        observation=f"Agent session infrastructure ready: {', '.join(obs_parts)}",
                        recommendation=f"Plan agent sessions using {env_types}: 1) Pilot with 20-50 users to measure session patterns, 2) {'Use dev/sandbox for load testing, production when ready for scale' if env_prod > 0 or env_sandbox > 0 else 'Test in current environment, plan production setup for enterprise deployment'}, 3) Monitor duration/concurrency, 4) Scale based on measured needs. Target: Pilot within 30 days, metrics within 60 days.",
                        link_text="Agent Session Basics",
                        link_url="https://learn.microsoft.com/microsoft-copilot-studio/analytics-sessions",
                        priority="Medium",
                        status="Success"
                    )
            elif user_count > 100:
                deployment_rec = new_recommendation(
                    service="Copilot Studio",
                    feature=f"{feature_name} - Session Capacity Estimation",
                    observation=f"Organization has {user_count} users - estimate agent session capacity needs",
                    recommendation=f"Measure session capacity with a bounded cohort selected from the {user_count} potential users. Record actual duration, peak concurrency, failure and abandon rates; use authorized Power Platform analytics where available. Let the customer define acceptable service levels, capacity headroom, review timing, and the expand/stop decision instead of assuming a fixed concurrency rate.",
                    link_text="Session Planning",
                    link_url="https://learn.microsoft.com/microsoft-copilot-studio/requirements-quotas",
                    priority="Medium",
                    status="Success"
                )
            else:
                deployment_rec = new_recommendation(
                    service="Copilot Studio",
                    feature=f"{feature_name} - Agent Session Planning",
                    observation="Virtual Agent User Session License active - plan session capacity",
                    recommendation="Plan agent session capacity: Start with pilot to measure concurrent needs, monitor analytics, scale based on usage. USL enables concurrent conversations.",
                    link_text="Session Capacity",
                    link_url="https://learn.microsoft.com/microsoft-copilot-studio/",
                    priority="Low",
                    status="Success"
                )
        else:
            deployment_rec = new_recommendation(
                service="Copilot Studio",
                feature=f"{feature_name} - Session Planning",
                observation="User Session License available",
                recommendation="Explore session capacity planning: Monitor concurrent sessions, plan for peak usage, implement overflow handling.",
                link_text="Session Management",
                link_url="https://learn.microsoft.com/microsoft-copilot-studio/",
                priority="Low",
                status="Success"
            )
        return [license_rec, deployment_rec]
    return [license_rec]

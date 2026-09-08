"""
AI Builder Models - Copilot AI Capability Leverage
This is a pseudo-feature to identify existing AI capabilities that can be leveraged in Copilot scenarios
"""
from Core.new_recommendation import (
    CATEGORY_SCAN_COVERAGE,
    NOT_ASSESSED_STATUS,
    new_recommendation,
)

async def get_recommendation(sku_name, status="Success", client=None, pp_client=None, pp_insights=None):
    """
    Analyze AI Builder models for Copilot integration opportunities.
    This runs when any Power Platform license is active.
    """
    ai_summary = getattr(pp_client, 'ai_model_summary', {}) if pp_client else {}

    # An unread inventory is assessment coverage, not a successful zero-model result.
    if not pp_client or not pp_insights or 'error' in ai_summary:
        error_msg = ai_summary.get(
            'error',
            'live Power Platform deployment data was not collected with an authorized delegated account',
        )
        
        return [new_recommendation(
            service="Power Platform",
            feature="AI Builder - Assessment Needed",
            observation=f"AI Builder model inventory was not assessed: {error_msg}. This does not mean that the tenant has no AI Builder models.",
            recommendation="Prefer a unified Power Platform inventory export supplied with --power-platform-inventory PATH, or explicitly opt in to the preview inventory collector after assigning tenant-scoped Power Platform Reader RBAC to the application. Use the deeper interactive Power Platform Administrator collection only when environment-level configuration review is required. Inventory is optional extensibility evidence and does not alter core readiness.",
            link_text="AI Builder Overview",
            link_url="https://learn.microsoft.com/ai-builder/overview",
            priority="Medium",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            disposition="Coverage",
        )]
    
    total_models = pp_insights.get('ai_models_total', ai_summary.get('total', 0))
    
    # No AI models - opportunity to build
    if total_models == 0:
        return [new_recommendation(
            service="Power Platform",
            feature="AI Builder - Get Started with Copilot AI",
            observation="No AI Builder models deployed - opportunity to add intelligent automation to Copilot workflows",
            recommendation="Build AI Builder models to enhance Copilot capabilities: 1) Start with document processing (invoice extraction, receipt scanning, form recognition) - integrate with Copilot for 'Process this invoice' prompts, 2) Create prediction models for business outcomes (lead scoring, churn prediction) - Copilot can query predictions via natural language, 3) Build text classification for email/document categorization - automate Copilot responses based on content type, 4) Use entity extraction to identify key information in conversations. Target: 2-3 production models in first 90 days. AI Builder makes Copilot responses smarter and more automated.",
            link_text="AI Builder for Copilot",
            link_url="https://learn.microsoft.com/ai-builder/overview",
            priority="Medium",
            status="Success"
        )]
    
    # Has AI models - identify integration opportunities
    doc_processing = len(ai_summary.get('document_processing', []))
    prediction = len(ai_summary.get('prediction', []))
    text_classification = len(ai_summary.get('text_classification', []))
    form_processing = len(ai_summary.get('form_processing', []))
    entity_extraction = len(ai_summary.get('entity_extraction', []))
    trained_models = ai_summary.get('trained_models', 0)
    published_models = ai_summary.get('published_models', 0)
    
    # Models exist but not published
    if total_models > 0 and published_models == 0:
        return [new_recommendation(
            service="Power Platform",
            feature="AI Builder - Publish Models for Copilot",
            observation=f"You have {total_models} AI Builder model(s) but NONE published - they cannot be used by Copilot or agents",
            recommendation=f"Publish your {trained_models} trained model(s) to make them available for Copilot integration: 1) Review and test each model's accuracy, 2) Publish models to production, 3) Create Power Automate flows that use these models with HTTP triggers (Copilot plugins), 4) Document model inputs/outputs for Copilot prompt engineering, 5) Monitor model performance post-deployment. Unpublished models are invisible to Copilot - publishing unlocks their value. Priority: Document processing models for invoice/receipt automation, then predictions for decision support.",
            link_text="Publish AI Builder Models",
            link_url="https://learn.microsoft.com/ai-builder/publish-model",
            priority="High",
            status="Success"
        )]
    
    # Has published models - maximize usage
    recommendations = []
    
    if doc_processing > 0:
        recommendations.append(new_recommendation(
            service="Power Platform",
            feature="AI Builder - Document Processing Integration",
            observation=f"You have {doc_processing} document processing model(s) - ready for Copilot 'Process document' automation",
            recommendation=f"Leverage {doc_processing} document processing model(s) in Copilot workflows: 1) Create Power Automate flows with HTTP triggers that call these models (users can prompt 'Process this invoice'), 2) Integrate with SharePoint/OneDrive for automatic document processing when users upload files, 3) Build Power Apps with Copilot Control that use these models for real-time document scanning, 4) Create agents that guide users through document submission and auto-extract data. Document processing is highly visible to users and shows immediate AI value. Priority use cases: Invoice processing, receipt scanning, form digitization.",
            link_text="Document Processing with Copilot",
            link_url="https://learn.microsoft.com/ai-builder/form-processing-model-overview",
            priority="Medium",
            status="Success"
        ))
    
    if prediction > 0:
        recommendations.append(new_recommendation(
            service="Power Platform",
            feature="AI Builder - Prediction Model Integration",
            observation=f"You have {prediction} prediction model(s) - enable Copilot to provide AI-powered recommendations",
            recommendation=f"Integrate {prediction} prediction model(s) with Copilot for intelligent decision support: 1) Create Copilot plugins that query predictions ('What's the churn risk for customer X?'), 2) Embed predictions in Power Apps that agents use for data-driven recommendations, 3) Build automated workflows that trigger actions based on prediction scores, 4) Surface prediction confidence scores in Copilot responses. Prediction models make Copilot responses data-driven rather than just informational. Use cases: Lead scoring, churn prediction, demand forecasting, risk assessment.",
            link_text="Prediction Models in Power Platform",
            link_url="https://learn.microsoft.com/ai-builder/prediction-overview",
            priority="Medium",
            status="Success"
        ))
    
    if text_classification > 0:
        recommendations.append(new_recommendation(
            service="Power Platform",
            feature="AI Builder - Text Classification Integration",
            observation=f"You have {text_classification} text classification model(s) - automate Copilot content routing",
            recommendation=f"Use {text_classification} text classification model(s) to make Copilot smarter: 1) Classify user prompts to route to specialized agents (sales vs. support vs. HR), 2) Analyze sentiment in customer feedback for priority escalation, 3) Categorize emails/documents automatically before Copilot processing, 4) Build content moderation for agent responses. Text classification enables context-aware Copilot routing and intelligent content handling. High-value scenarios: Email classification, support ticket routing, sentiment-based prioritization.",
            link_text="Text Classification Models",
            link_url="https://learn.microsoft.com/ai-builder/text-classification-overview",
            priority="Low",
            status="Success"
        ))
    
    # If we have recommendations, return them; otherwise return summary
    if recommendations:
        return recommendations
    
    # Generic recommendation for other model types
    return [new_recommendation(
        service="Power Platform",
        feature="AI Builder - Copilot AI Enhancement",
        observation=f"You have {total_models} AI Builder model(s) ({published_models} published) - integrate with Copilot for intelligent automation",
        recommendation=f"Maximize value from {published_models} published AI model(s): 1) Create Copilot plugins (Power Automate flows with HTTP triggers) that invoke these models, 2) Embed models in Power Apps with Copilot Control for interactive AI experiences, 3) Build agents that use AI models for decision support and automation, 4) Monitor model performance and retrain quarterly. AI Builder models transform Copilot from conversational to intelligent - they enable automated extraction, prediction, and classification. Document each model's Copilot integration pattern for user discovery.",
        link_text="AI Builder Integration Patterns",
        link_url="https://learn.microsoft.com/ai-builder/use-in-flow-overview",
        priority="Medium",
        status="Success"
    )]

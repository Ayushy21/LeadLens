from lead_enricher.config import Settings
from lead_enricher.models import Usage


def estimate_cost(usage: Usage, settings: Settings) -> None:
    usage.estimated_llm_cost_usd = None
    if usage.request_count == 0:
        usage.cost_reason = "No provider requests; no billed usage observed"
        return
    if settings.pricing_model != settings.model_name:
        usage.cost_reason = "Pricing absent or does not match configured model"
        return
    if any(
        m != settings.model_name and not m.startswith(settings.model_name + "-")
        for m in usage.models
    ):
        usage.cost_reason = "Provider model does not match pricing model"
        return
    rates = (
        settings.input_price_per_million,
        settings.cached_input_price_per_million,
        settings.output_price_per_million,
    )
    if any(rate is None for rate in rates):
        usage.cost_reason = "Pricing rates unavailable"
        return
    if usage.reported_response_count == 0:
        usage.cost_reason = "No provider-reported usage; billed amount unknown"
        return
    assert rates[0] is not None and rates[1] is not None and rates[2] is not None
    usage.estimated_llm_cost_usd = round(
        (
            (usage.input_tokens - usage.cached_input_tokens) * rates[0]
            + usage.cached_input_tokens * rates[1]
            + usage.output_tokens * rates[2]
        )
        / 1_000_000,
        10,
    )
    usage.cost_reason = (
        "Estimate from provider-reported usage and configured USD prices"
        if usage.accounting_complete
        else "INCOMPLETE: estimate covers known usage only; failed-request billing unknown"
    )

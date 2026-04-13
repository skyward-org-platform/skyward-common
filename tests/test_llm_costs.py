"""Tests for LLM cost calculation functions."""
import pytest
from skyward.llm.costs import (
    calculate_cost,
    estimate_batch_cost,
    format_cost,
    summarize_costs,
    OPENAI_COSTS,
    GEMINI_COSTS,
    PERPLEXITY_COSTS,
)


# ══════════════════════════════════════════════════════════════════════════════
# calculate_cost
# ══════════════════════════════════════════════════════════════════════════════

class TestCalculateCost:

    def test_gpt4o_known_pricing(self):
        # gpt-4o: input=2.50, output=10.00 per 1M tokens
        cost = calculate_cost(1_000_000, 1_000_000, "gpt-4o", "openai")
        assert cost == pytest.approx(12.50)

    def test_gpt4o_mini_small_usage(self):
        # gpt-4o-mini: input=0.15, output=0.60 per 1M tokens
        # 1000 input, 500 output
        cost = calculate_cost(1000, 500, "gpt-4o-mini", "openai")
        expected = (1000 * 0.15 / 1_000_000) + (500 * 0.60 / 1_000_000)
        assert cost == pytest.approx(expected)

    def test_zero_tokens_zero_cost(self):
        cost = calculate_cost(0, 0, "gpt-4o", "openai")
        assert cost == 0.0

    def test_input_only(self):
        cost = calculate_cost(1_000_000, 0, "gpt-4o", "openai")
        assert cost == pytest.approx(2.50)

    def test_output_only(self):
        cost = calculate_cost(0, 1_000_000, "gpt-4o", "openai")
        assert cost == pytest.approx(10.00)

    def test_gemini_flash_pricing(self):
        # gemini-2.0-flash: input=0.075, output=0.30
        cost = calculate_cost(1_000_000, 1_000_000, "gemini-2.0-flash", "gemini")
        assert cost == pytest.approx(0.375)

    def test_gemini_free_preview(self):
        # gemini-2.0-flash-exp: free during preview
        cost = calculate_cost(1_000_000, 1_000_000, "gemini-2.0-flash-exp", "gemini")
        assert cost == 0.0

    def test_perplexity_sonar(self):
        # sonar: input=1.00, output=1.00
        cost = calculate_cost(1_000_000, 1_000_000, "sonar", "perplexity")
        assert cost == pytest.approx(2.00)

    def test_unknown_model_uses_provider_default(self):
        # Unknown openai model defaults to gpt-4o pricing (2.50, 10.00)
        cost = calculate_cost(1_000_000, 1_000_000, "nonexistent-model", "openai")
        assert cost == pytest.approx(12.50)

    def test_unknown_provider_uses_conservative_estimate(self):
        # Unknown provider defaults to (2.50, 10.00)
        cost = calculate_cost(1_000_000, 1_000_000, "anything", "unknown_provider")
        assert cost == pytest.approx(12.50)

    def test_expensive_model_gpt5_2_pro(self):
        # gpt-5.2-pro: input=21.00, output=168.00 (reasoning model)
        cost = calculate_cost(1_000_000, 1_000_000, "gpt-5.2-pro", "openai")
        assert cost == pytest.approx(189.00)

    def test_all_openai_models_have_pricing(self):
        """Every model in the cost table should return non-default pricing."""
        for model in OPENAI_COSTS:
            cost = calculate_cost(1_000_000, 0, model, "openai")
            input_price = OPENAI_COSTS[model][0]
            assert cost == pytest.approx(input_price)

    def test_all_gemini_models_have_pricing(self):
        for model in GEMINI_COSTS:
            cost = calculate_cost(1_000_000, 0, model, "gemini")
            input_price = GEMINI_COSTS[model][0]
            assert cost == pytest.approx(input_price)

    def test_all_perplexity_models_have_pricing(self):
        for model in PERPLEXITY_COSTS:
            cost = calculate_cost(1_000_000, 0, model, "perplexity")
            input_price = PERPLEXITY_COSTS[model][0]
            assert cost == pytest.approx(input_price)


# ══════════════════════════════════════════════════════════════════════════════
# estimate_batch_cost
# ══════════════════════════════════════════════════════════════════════════════

class TestEstimateBatchCost:

    def test_num_items_basic(self):
        result = estimate_batch_cost(num_items=10, avg_input_tokens=1000, avg_output_tokens=500, model="gpt-4o")
        assert "total_cost" in result
        assert "cost_per_item" in result
        assert "estimated_input_tokens" in result
        assert "estimated_output_tokens" in result
        assert result["estimated_input_tokens"] == 10_000
        assert result["estimated_output_tokens"] == 5_000

    def test_num_pages_with_questions(self):
        result = estimate_batch_cost(num_pages=5, questions_per_page=3, avg_input_tokens=2000, avg_output_tokens=1000)
        # 5 pages * 3 questions = 15 total
        assert result["estimated_input_tokens"] == 30_000
        assert result["estimated_output_tokens"] == 15_000

    def test_neither_num_items_nor_pages_raises(self):
        with pytest.raises(ValueError, match="Either num_items or num_pages"):
            estimate_batch_cost()

    def test_cost_per_item_calculation(self):
        result = estimate_batch_cost(num_items=100, avg_input_tokens=1000, avg_output_tokens=500, model="gpt-4o")
        assert result["cost_per_item"] == pytest.approx(result["total_cost"] / 100)

    def test_single_item(self):
        result = estimate_batch_cost(num_items=1, avg_input_tokens=3000, avg_output_tokens=1000, model="gpt-4o")
        expected_cost = calculate_cost(3000, 1000, "gpt-4o", "openai")
        assert result["total_cost"] == pytest.approx(expected_cost)

    def test_gemini_provider(self):
        result = estimate_batch_cost(num_items=10, model="gemini-2.5-flash", provider="gemini")
        assert result["total_cost"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# format_cost
# ══════════════════════════════════════════════════════════════════════════════

class TestFormatCost:

    def test_tiny_cost_four_decimals(self):
        assert format_cost(0.001) == "$0.0010"

    def test_small_cost_three_decimals(self):
        assert format_cost(0.05) == "$0.050"

    def test_medium_cost_three_decimals(self):
        assert format_cost(0.999) == "$0.999"

    def test_large_cost_two_decimals(self):
        assert format_cost(1.50) == "$1.50"

    def test_zero_cost(self):
        assert format_cost(0.0) == "$0.0000"

    def test_boundary_at_one_cent(self):
        # Just below 0.01 → 4 decimals
        assert format_cost(0.009) == "$0.0090"
        # At 0.01 → 3 decimals
        assert format_cost(0.01) == "$0.010"

    def test_boundary_at_one_dollar(self):
        # Just below 1.00 → 3 decimals
        assert format_cost(0.99) == "$0.990"
        # At 1.00 → 2 decimals
        assert format_cost(1.00) == "$1.00"

    def test_large_value(self):
        assert format_cost(150.00) == "$150.00"


# ══════════════════════════════════════════════════════════════════════════════
# summarize_costs
# ══════════════════════════════════════════════════════════════════════════════

class TestSummarizeCosts:

    def test_dict_based_interface(self):
        token_counts = {
            "step_a": (1_000_000, 500_000),
            "step_b": (500_000, 250_000),
        }
        models = {
            "step_a": ("gpt-4o", "openai"),
            "step_b": ("gpt-4o-mini", "openai"),
        }
        result = summarize_costs(token_counts=token_counts, models=models)
        assert "step_a_cost" in result
        assert "step_b_cost" in result
        assert "total_cost" in result
        assert result["total_cost"] == pytest.approx(result["step_a_cost"] + result["step_b_cost"])

    def test_dict_based_defaults_to_gpt4o(self):
        token_counts = {"step": (1_000_000, 1_000_000)}
        result = summarize_costs(token_counts=token_counts)
        # No models dict → defaults to gpt-4o
        expected = calculate_cost(1_000_000, 1_000_000, "gpt-4o", "openai")
        assert result["step_cost"] == pytest.approx(expected)

    def test_faq_pipeline_enrichment_only(self):
        result = summarize_costs(enrichment_tokens=(10000, 5000))
        assert "enrichment_cost" in result
        assert "total_cost" in result
        assert result["enrichment_cost"] == result["total_cost"]

    def test_faq_pipeline_all_steps(self):
        result = summarize_costs(
            enrichment_tokens=(10000, 5000),
            answer_tokens=(20000, 10000),
            fact_check_tokens=(15000, 8000),
        )
        assert "enrichment_cost" in result
        assert "answer_cost" in result
        assert "fact_check_cost" in result
        total = result["enrichment_cost"] + result["answer_cost"] + result["fact_check_cost"]
        assert result["total_cost"] == pytest.approx(total)

    def test_faq_pipeline_no_tokens_returns_zero(self):
        result = summarize_costs()
        assert result["total_cost"] == 0.0

    def test_faq_pipeline_custom_models(self):
        result = summarize_costs(
            answer_tokens=(1_000_000, 1_000_000),
            answer_model="gpt-5.2-pro",
            answer_provider="openai",
        )
        expected = calculate_cost(1_000_000, 1_000_000, "gpt-5.2-pro", "openai")
        assert result["answer_cost"] == pytest.approx(expected)

    def test_mixed_providers_via_dict(self):
        token_counts = {
            "generate": (10000, 5000),
            "verify": (8000, 3000),
        }
        models = {
            "generate": ("gpt-4o", "openai"),
            "verify": ("sonar", "perplexity"),
        }
        result = summarize_costs(token_counts=token_counts, models=models)
        generate_expected = calculate_cost(10000, 5000, "gpt-4o", "openai")
        verify_expected = calculate_cost(8000, 3000, "sonar", "perplexity")
        assert result["generate_cost"] == pytest.approx(generate_expected)
        assert result["verify_cost"] == pytest.approx(verify_expected)


class TestAnthropicAndGrokCosts:

    def test_anthropic_sonnet_pricing(self):
        from skyward.llm.costs import ANTHROPIC_COSTS
        assert "claude-sonnet-4-20250514" in ANTHROPIC_COSTS
        cost = calculate_cost(1_000_000, 1_000_000, "claude-sonnet-4-20250514", "anthropic")
        assert cost > 0

    def test_grok_pricing(self):
        from skyward.llm.costs import GROK_COSTS
        assert "grok-3" in GROK_COSTS
        cost = calculate_cost(1_000_000, 1_000_000, "grok-3", "grok")
        assert cost > 0

    def test_all_anthropic_models_have_pricing(self):
        from skyward.llm.costs import ANTHROPIC_COSTS
        for model in ANTHROPIC_COSTS:
            cost = calculate_cost(1_000_000, 0, model, "anthropic")
            input_price = ANTHROPIC_COSTS[model][0]
            assert cost == pytest.approx(input_price)

    def test_all_grok_models_have_pricing(self):
        from skyward.llm.costs import GROK_COSTS
        for model in GROK_COSTS:
            cost = calculate_cost(1_000_000, 0, model, "grok")
            input_price = GROK_COSTS[model][0]
            assert cost == pytest.approx(input_price)

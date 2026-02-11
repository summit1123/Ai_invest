from __future__ import annotations

import unittest

from ai_invest.agents.prompt_contract import (
    finance_monthly_system_prompt,
    governance_coordinator_instructions,
    governance_critique_instructions,
    governance_ops_instructions,
    governance_quant_instructions,
    governance_research_instructions,
    governance_risk_instructions,
    governance_secretary_instructions,
    research_daily_system_prompt,
    secretary_minutes_system_prompt,
    strategy_trade_plan_system_prompt,
    strategy_weekly_priority_system_prompt,
)


REQUIRED_HEADERS = [
    "[ROLE]",
    "[OBJECTIVE]",
    "[INPUT_CONTRACT]",
    "[HARD_RULES]",
    "[OUTPUT_SCHEMA]",
    "[FAILSAFE]",
]


class PromptContractTests(unittest.TestCase):
    def _assert_required_headers(self, prompt: str) -> None:
        for h in REQUIRED_HEADERS:
            self.assertIn(h, prompt)

    def test_all_prompt_contracts_have_required_headers(self) -> None:
        prompts = [
            governance_research_instructions(),
            governance_quant_instructions(),
            governance_risk_instructions(),
            governance_ops_instructions(),
            governance_critique_instructions(),
            governance_coordinator_instructions(),
            governance_secretary_instructions(),
            research_daily_system_prompt(),
            strategy_trade_plan_system_prompt(),
            strategy_weekly_priority_system_prompt(),
            secretary_minutes_system_prompt(),
            finance_monthly_system_prompt(),
        ]
        for p in prompts:
            self._assert_required_headers(p)

    def test_governance_quant_contract_includes_symbol_and_json_rules(self) -> None:
        p = governance_quant_instructions()
        self.assertIn("allowed_symbols 밖의 심볼 선택 금지", p)
        self.assertIn("스키마 JSON만 출력", p)

    def test_secretary_contract_is_text_not_json(self) -> None:
        p = governance_secretary_instructions()
        self.assertIn("일반 텍스트만 출력(JSON 금지)", p)


if __name__ == "__main__":
    unittest.main()

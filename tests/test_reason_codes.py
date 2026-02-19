from __future__ import annotations

import unittest

from ai_invest.domain.reason_codes import (
    ReasonCode,
    ReasonDomain,
    domain_of,
    parse_reason_code,
    validate_reason_codes,
)


class ReasonCodesTests(unittest.TestCase):
    def test_parse_and_domain_success(self) -> None:
        code = parse_reason_code("RG_RECON_FAIL")
        self.assertEqual(code, ReasonCode.RG_RECON_FAIL)
        self.assertEqual(domain_of(code), ReasonDomain.SAFE_JUDGE)

    def test_validate_reason_codes_limits(self) -> None:
        with self.assertRaises(ValueError):
            validate_reason_codes(
                [
                    "RG_PASS",
                    "RG_EDGE_TOO_LOW",
                    "RG_SPREAD_TOO_WIDE",
                    "RG_COOLDOWN_ACTIVE",
                ]
            )

    def test_validate_reason_codes_allowed_domains(self) -> None:
        with self.assertRaises(ValueError):
            validate_reason_codes(
                ["EX_ORDER_REJECTED"],
                allowed_domains={ReasonDomain.SAFE_JUDGE},
            )

    def test_new_micro_block_codes_parse(self) -> None:
        self.assertEqual(parse_reason_code("RG_MICRO_BLOCKED_COOLDOWN"), ReasonCode.RG_MICRO_BLOCKED_COOLDOWN)
        self.assertEqual(parse_reason_code("RG_MICRO_BLOCKED_EDGE"), ReasonCode.RG_MICRO_BLOCKED_EDGE)
        self.assertEqual(parse_reason_code("RG_MICRO_BLOCKED_POLICY"), ReasonCode.RG_MICRO_BLOCKED_POLICY)


if __name__ == "__main__":
    unittest.main()

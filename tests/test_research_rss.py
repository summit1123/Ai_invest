from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_invest.research.rss import NewsHeadline, fetch_crypto_headlines, fetch_web_search_headlines


class ResearchRssTests(unittest.TestCase):
    def test_fetch_crypto_headlines_merges_web_search_when_enabled(self) -> None:
        rss_batches = [
            [NewsHeadline(source="Google News", title="g", url="https://n.example/g", published_at=None)],
            [NewsHeadline(source="CoinDesk", title="d", url="https://n.example/d", published_at=None)],
            [NewsHeadline(source="Cointelegraph", title="c", url="https://n.example/c", published_at=None)],
        ]
        web_batch = [NewsHeadline(source="Bing News KR", title="b", url="https://n.example/b", published_at=None)]

        with (
            patch("ai_invest.research.rss.fetch_rss_headlines", side_effect=rss_batches),
            patch("ai_invest.research.rss.fetch_web_search_headlines", return_value=web_batch),
        ):
            out = fetch_crypto_headlines(
                symbol="KRW-BTC",
                limit=10,
                include_web_search=True,
                web_search_provider="bing_news_rss",
                web_search_limit=4,
                web_search_timeout_sec=8,
                rss_timeout_sec=7,
            )

        self.assertEqual(len(out), 4)
        channels = {str(row.get("source")): str(row.get("channel")) for row in out}
        self.assertEqual(channels.get("Bing News KR"), "web_search")
        self.assertEqual(channels.get("CoinDesk"), "rss")

    def test_fetch_web_search_auto_prefers_wqb_when_available(self) -> None:
        wqb_batch = [NewsHeadline(source="WQB", title="w", url="https://n.example/w", published_at=None)]
        with (
            patch("ai_invest.research.rss.fetch_wqb_headlines", return_value=wqb_batch),
            patch("ai_invest.research.rss._fetch_bing_news_headlines") as mocked_bing,
        ):
            out = fetch_web_search_headlines(query="bitcoin", provider="auto", limit=6, timeout_sec=9)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source, "WQB")
        mocked_bing.assert_not_called()


if __name__ == "__main__":
    unittest.main()

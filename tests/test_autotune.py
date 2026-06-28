import importlib.util
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("autotune", Path(__file__).parents[1] / "scripts" / "autotune.py")
autotune = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(autotune)


class AutotuneTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "domains": ["one.example", "two.example"],
            "protocols": ["http", "https", "quic"],
            "repeats": 2,
            "scan_level": "quick",
            "test_set": "standard",
        }

    def test_parse_prefers_domain_coverage(self):
        lines = [
            "curl_test_http ipv4 one.example : nfqws2 --qnum=123 --wf-tcp-out=80 --payload=http_req --lua-desync=multisplit:pos=2",
            "curl_test_http ipv4 two.example : nfqws2 --qnum=123 --wf-tcp-out=80 --payload=http_req --lua-desync=multisplit:pos=2",
            "http ipv4 one.example : nfqws2 --payload=http_req --lua-desync=fake:blob=x:repeats=8",
            "https_tls12 ipv4 one.example : nfqws2 --payload=tls_client_hello --lua-desync=multidisorder:pos=1",
        ]
        result = autotune.parse_results(lines, self.request)
        self.assertEqual(result[0]["protocol"], "http")
        self.assertEqual(result[0]["coverage"], 1.0)
        self.assertNotIn("qnum", result[0]["strategy"])
        self.assertNotIn("wf-tcp", result[0]["strategy"])

    def test_rejects_unsafe_strategy(self):
        self.assertIsNone(autotune.clean_strategy("--payload=x;touch /tmp/pwned"))
        self.assertIsNone(autotune.clean_strategy("--payload=x $(id)"))

    def test_render_profile_keeps_safety_invariants(self):
        job = {"id": "20260622T120000-abcdef"}
        selected = [{"protocol": "https", "strategy": "--payload=tls_client_hello --lua-desync=multisplit:pos=1"}]
        name, content = autotune.render_profile(job, selected)
        self.assertEqual(name, "autotune")
        self.assertIn('PROFILE_DESCRIPTION="Автоматически подобранные стратегии"', content)
        self.assertIn("NFQWS2_PORTS_TCP=443", content)
        self.assertIn("NFQWS2_PORTS_UDP=", content)
        self.assertNotIn("PKT_IN", content)

    def test_candidate_rating_and_selection_use_attempt_success_and_domain_coverage(self):
        stats = {
            ("https", "--payload=x --lua-desync=one"): {
                "protocol": "https",
                "strategy": "--payload=x --lua-desync=one",
                "attempts": 4,
                "successes": 3,
                "domains": {
                    "one.example": {"attempts": 2, "successes": 2},
                    "two.example": {"attempts": 2, "successes": 1},
                },
                "last_seen": 1,
            },
            ("https", "--payload=x --lua-desync=two"): {
                "protocol": "https",
                "strategy": "--payload=x --lua-desync=two",
                "attempts": 2,
                "successes": 2,
                "domains": {"one.example": {"attempts": 2, "successes": 2}},
                "last_seen": 2,
            },
        }
        rows = autotune.candidate_rows(stats, self.request)
        self.assertEqual(rows[0]["suitability"], 75)
        self.assertEqual(rows[0]["coverage"], 1.0)
        selected = autotune.select_from_candidates(stats, self.request)
        self.assertEqual(selected[0]["strategy"], "--payload=x --lua-desync=one")

    def test_failed_candidates_are_not_reported_as_available_without_bypass(self):
        note = autotune.explain_empty_result(["!!!!! AVAILABLE !!!!!", "curl_test_https_tls12 ipv4 one.example : nfqws2 not working"], tested=9)
        self.assertIn("Ни одна", note)

    def test_candidate_parser_stops_after_strategy_outcome(self):
        lines = [
            "- curl_test_https_tls12 ipv4 one.example : nfqws2 --payload=x --lua-desync=one",
            "[attempt 1] AVAILABLE",
            "[attempt 2] AVAILABLE",
            "!!!!! working strategy found !!!!!",
            "[attempt 1] AVAILABLE",
            "- curl_test_http3 ipv4 one.example : nfqws2 --payload=y --lua-desync=two",
            "curl: timeout",
            "UNAVAILABLE code=28",
            "[attempt 1] AVAILABLE",
        ]
        stats = autotune.parse_candidate_attempts(lines, self.request)
        self.assertEqual(stats[("https", "--payload=x --lua-desync=one")]["attempts"], 2)
        self.assertEqual(stats[("https", "--payload=x --lua-desync=one")]["successes"], 2)
        self.assertEqual(stats[("quic", "--payload=y --lua-desync=two")]["attempts"], 1)

    def test_monitor_triggers_only_when_accepted_availability_degrades(self):
        domains = ["good.example", "accepted-down.example", "improved.example"]
        baseline = {
            "good.example": {"available": True},
            "accepted-down.example": {"available": False},
            "improved.example": {"available": False},
        }
        current = {
            "good.example": {"available": False},
            "accepted-down.example": {"available": False},
            "improved.example": {"available": True},
        }
        self.assertEqual(
            autotune.find_degraded_domains(baseline, current, domains),
            ["good.example"],
        )

    def test_monitor_rejects_private_probe_destinations(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]
        with patch.object(autotune.socket, "getaddrinfo", return_value=answers):
            self.assertEqual(autotune.public_ipv4_addresses("example.org"), ["93.184.216.34"])


if __name__ == "__main__":
    unittest.main()

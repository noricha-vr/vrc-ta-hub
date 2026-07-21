import importlib.util
import json
import sys
from datetime import date, time, timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from community.activity_monitoring import (
    STATUS_EXPLICITLY_ENDED,
    STATUS_NO_RECENT_EVIDENCE,
    archive_inactive_community,
    extract_x_handle,
    extract_x_status_handle,
    get_activity_candidates,
)
from community.models import Community
from event.models import Event
from event.tests.tweet_generation import TweetGenerationPatchMixin

TODAY = date(2026, 7, 21)
MONITOR_SETTINGS = {
    "COMMUNITY_ACTIVITY_MONITOR_TOKEN": "monitor-token",
    "COMMUNITY_ACTIVITY_INACTIVE_DAYS": 90,
    "COMMUNITY_ACTIVITY_REQUIRED_CHECKS": 2,
    "COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE": 0.75,
    "COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE": 0.90,
}


def load_monitor_script():
    script_path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "community_activity_monitor.py"
    )
    module_name = "community_activity_monitor_script_for_tests"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("community activity monitor script could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@override_settings(**MONITOR_SETTINGS)
class CommunityActivityMonitoringTest(TweetGenerationPatchMixin, TestCase):
    def setUp(self):
        self.community = Community.objects.create(
            name="活動監視テスト集会",
            status="approved",
            frequency="隔週",
            organizers="主催者",
            poster_image="poster/activity-monitor.jpg",
            sns_url="https://x.com/activity_test",
            twitter_hashtag="#活動監視テスト",
            tags=["tech"],
        )
        self._set_updated_at(self.community, TODAY - timedelta(days=100))
        self.url = reverse("community:activity_monitor")

    def _set_updated_at(self, community, value):
        Community.objects.filter(pk=community.pk).update(updated_at=value)
        community.refresh_from_db()

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_candidate_uses_schedule_dates_only_as_identity_hints(self, _mock_date):
        past_date = TODAY - timedelta(days=120)
        future_date = TODAY + timedelta(days=30)
        Event.objects.create(
            community=self.community,
            date=past_date,
            start_time=time(22, 0),
            duration=60,
        )
        Event.objects.create(
            community=self.community,
            date=future_date,
            start_time=time(22, 0),
            duration=60,
        )

        candidates = get_activity_candidates(inactive_days=90)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].last_scheduled_event_date, past_date)
        self.assertEqual(candidates[0].next_scheduled_event_date, future_date)

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_candidate_does_not_require_a_poster(self, _mock_date):
        Community.objects.filter(pk=self.community.pk).update(poster_image="")

        candidate_ids = {
            candidate.community_id
            for candidate in get_activity_candidates(inactive_days=90)
        }

        self.assertIn(self.community.pk, candidate_ids)

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_future_generated_event_does_not_prevent_monitoring(self, _mock_date):
        Event.objects.create(
            community=self.community,
            date=TODAY + timedelta(days=60),
            start_time=time(22, 0),
            duration=60,
        )

        candidate_ids = {
            candidate.community_id
            for candidate in get_activity_candidates(inactive_days=90)
        }

        self.assertIn(self.community.pk, candidate_ids)

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_recent_metadata_update_and_partner_only_are_excluded(self, _mock_date):
        self._set_updated_at(self.community, TODAY - timedelta(days=10))
        partner = Community.objects.create(
            name="協力団体",
            status="approved",
            frequency="不定期",
            organizers="partner",
            poster_image="poster/partner.jpg",
            sns_url="https://x.com/partner_test",
            tags=["partner"],
        )
        self._set_updated_at(partner, TODAY - timedelta(days=120))

        self.assertEqual(get_activity_candidates(inactive_days=90), [])

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_service_does_not_allow_shorter_inactive_period(self, _mock_date):
        self._set_updated_at(self.community, TODAY - timedelta(days=70))

        result = archive_inactive_community(
            community_id=self.community.pk,
            status=STATUS_NO_RECENT_EVIDENCE,
            confidence=0.95,
            evidence_urls=[],
            consecutive_inactive_checks=2,
            inactive_days=60,
        )

        self.assertFalse(result.archived)
        self.assertEqual(result.action, "ignored_recent_metadata_update")

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_archive_requires_two_consecutive_checks(self, _mock_date):
        result = archive_inactive_community(
            community_id=self.community.pk,
            status=STATUS_NO_RECENT_EVIDENCE,
            confidence=0.95,
            evidence_urls=[],
            consecutive_inactive_checks=1,
            inactive_days=90,
        )

        self.assertFalse(result.archived)
        self.assertEqual(result.action, "ignored_insufficient_evidence")
        self.community.refresh_from_db()
        self.assertIsNone(self.community.end_at)

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_soft_archive_keeps_future_events_for_recovery(self, _mock_date):
        future_event = Event.objects.create(
            community=self.community,
            date=TODAY + timedelta(days=60),
            start_time=time(22, 0),
            duration=60,
        )

        result = archive_inactive_community(
            community_id=self.community.pk,
            status=STATUS_NO_RECENT_EVIDENCE,
            confidence=0.95,
            evidence_urls=[],
            consecutive_inactive_checks=2,
            inactive_days=90,
        )

        self.assertTrue(result.archived)
        self.assertEqual(result.action, "archived_inactive")
        self.community.refresh_from_db()
        self.assertEqual(self.community.end_at, TODAY)
        self.assertTrue(Event.objects.filter(pk=future_event.pk).exists())
        self.assertNotContains(
            self.client.get(reverse("community:list")),
            self.community.name,
        )
        self.assertNotContains(
            self.client.get(reverse("event:list")),
            self.community.name,
        )

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_explicit_end_requires_post_evidence(self, _mock_date):
        without_evidence = archive_inactive_community(
            community_id=self.community.pk,
            status=STATUS_EXPLICITLY_ENDED,
            confidence=0.99,
            evidence_urls=[],
            consecutive_inactive_checks=2,
            inactive_days=90,
        )
        self.assertFalse(without_evidence.archived)
        self.assertEqual(
            without_evidence.action,
            "ignored_missing_official_end_evidence",
        )

        with_evidence = archive_inactive_community(
            community_id=self.community.pk,
            status=STATUS_EXPLICITLY_ENDED,
            confidence=0.99,
            evidence_urls=["https://x.com/activity_test/status/123456789"],
            consecutive_inactive_checks=2,
            inactive_days=90,
        )
        self.assertTrue(with_evidence.archived)
        self.assertEqual(with_evidence.action, "archived_explicitly_ended")

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_explicit_end_rejects_evidence_from_another_account(self, _mock_date):
        result = archive_inactive_community(
            community_id=self.community.pk,
            status=STATUS_EXPLICITLY_ENDED,
            confidence=0.99,
            evidence_urls=["https://x.com/another_account/status/123456789"],
            consecutive_inactive_checks=2,
            inactive_days=90,
        )

        self.assertFalse(result.archived)
        self.assertEqual(result.action, "ignored_missing_official_end_evidence")

    def test_extract_x_handle_rejects_non_profile_urls(self):
        self.assertEqual(extract_x_handle("https://x.com/Valid_Name"), "valid_name")
        self.assertEqual(extract_x_handle("https://x.com/i/status/123"), "")
        self.assertEqual(extract_x_handle("https://example.com/name"), "")
        self.assertEqual(
            extract_x_status_handle(
                "https://x.com/Valid_Name/status/123456789"
            ),
            "valid_name",
        )
        self.assertEqual(
            extract_x_status_handle("https://x.com/i/status/123456789"),
            "",
        )

    def test_endpoint_uses_dedicated_fail_closed_token(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(
            self.client.get(
                self.url,
                HTTP_REQUEST_TOKEN="wrong-token",
            ).status_code,
            401,
        )

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_endpoint_lists_candidates_and_disables_cache(self, _mock_date):
        response = self.client.get(
            self.url,
            HTTP_REQUEST_TOKEN="monitor-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(
            response.json()["communities"][0]["officialXHandle"],
            "activity_test",
        )

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_endpoint_does_not_allow_client_to_weaken_inactive_days(self, _mock_date):
        response = self.client.get(
            f"{self.url}?inactiveDays=60",
            HTTP_REQUEST_TOKEN="monitor-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inactiveDays"], 90)

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_endpoint_rejects_profile_url_as_explicit_evidence(self, _mock_date):
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "communityId": self.community.pk,
                    "status": STATUS_EXPLICITLY_ENDED,
                    "confidence": 0.99,
                    "evidenceUrls": ["https://x.com/activity_test"],
                    "consecutiveInactiveChecks": 2,
                    "inactiveDays": 90,
                }
            ),
            content_type="application/json",
            HTTP_REQUEST_TOKEN="monitor-token",
        )

        self.assertEqual(response.status_code, 400)
        self.community.refresh_from_db()
        self.assertIsNone(self.community.end_at)

    @patch("community.activity_monitoring.timezone.localdate", return_value=TODAY)
    def test_endpoint_archives_after_server_side_revalidation(self, _mock_date):
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "communityId": self.community.pk,
                    "status": STATUS_NO_RECENT_EVIDENCE,
                    "confidence": 0.95,
                    "evidenceUrls": [],
                    "consecutiveInactiveChecks": 2,
                    "inactiveDays": 90,
                }
            ),
            content_type="application/json",
            HTTP_REQUEST_TOKEN="monitor-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["archived"])
        self.community.refresh_from_db()
        self.assertEqual(self.community.end_at, TODAY)


class CommunityActivityMonitorScriptTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.monitor = load_monitor_script()

    def test_citation_verification_requires_an_x_status_url(self):
        verified = self.monitor.intersect_evidence_urls(
            [
                "https://x.com/example/status/123456789",
                "https://x.com/example",
            ],
            [
                "https://x.com/i/status/123456789",
                "https://x.com/example",
            ],
        )

        self.assertEqual(verified, ["https://x.com/example/status/123456789"])

    def test_hashtag_only_end_claim_is_not_accepted_as_explicit_end(self):
        assessment = self.monitor.Assessment(
            status="explicitly_ended",
            confidence=0.99,
            latest_activity_date=TODAY,
            summary="第三者が終了と投稿",
            evidence_urls=["https://x.com/third_party/status/123456789"],
            response_ids=["resp_1"],
            search_calls=1,
            cost_usd=0.01,
        )
        candidate = {
            "name": "テスト集会",
            "officialXHandle": "",
            "twitterHashtags": ["テスト集会"],
        }

        with patch.object(
            self.monitor,
            "call_x_search",
            return_value=assessment,
        ):
            result = self.monitor.analyze_candidate(
                session=None,
                xai_api_key="test",
                model="grok-4.5",
                reasoning_effort="low",
                candidate=candidate,
                from_date=TODAY - timedelta(days=90),
                to_date=TODAY,
            )

        self.assertEqual(result.status, "unknown")

    def test_discord_webhook_secret_is_redacted_from_logs(self):
        self.assertEqual(
            self.monitor.redact_url_for_logs(
                "https://discord.com/api/webhooks/123456/secret-token"
            ),
            "https://discord.com/api/webhooks/***",
        )

    def test_xai_response_without_status_citation_fails_closed(self):
        class FakeResponse:
            status_code = 200
            headers = {}
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "id": "resp_test",
                    "status": "completed",
                    "output": [
                        {"type": "x_search_call"},
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "status": "active",
                                            "confidence": 0.99,
                                            "latestActivityDate": "2026-07-20",
                                            "summary": "活動中",
                                            "evidenceUrls": [
                                                "https://x.com/example"
                                            ],
                                        }
                                    ),
                                    "annotations": [],
                                }
                            ],
                        },
                    ],
                    "citations": ["https://x.com/example"],
                    "usage": {"cost_in_usd_ticks": 100000000},
                }

        class FakeSession:
            def request(self, *_args, **_kwargs):
                return FakeResponse()

        result = self.monitor.call_x_search(
            session=FakeSession(),
            xai_api_key="test",
            model="grok-4.5",
            reasoning_effort="low",
            candidate={
                "name": "テスト集会",
                "officialXHandle": "example",
                "twitterHashtags": ["テスト集会"],
            },
            from_date=TODAY - timedelta(days=90),
            to_date=TODAY,
            scope="official",
            allowed_x_handles=["example"],
        )

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.evidence_urls, [])

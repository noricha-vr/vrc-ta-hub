import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.activity_models import CommunityActivityCheck, CommunityActivityState
from community.activity_client import (
    ActivityAssessment,
    XaiActivityClient,
    extract_x_handle,
    normalize_assessment,
)
from community.activity_monitor import run_community_activity_checks
from community.models import Community


MONITOR_SETTINGS = {
    "XAI_API_KEY": "xai-test",
    "XAI_ACTIVITY_MODEL": "grok-4.5",
    "XAI_ACTIVITY_TIMEOUT_SECONDS": 30,
    "COMMUNITY_ACTIVITY_LOOKBACK_DAYS": 90,
    "COMMUNITY_ACTIVITY_CHECK_INTERVAL_DAYS": 7,
    "COMMUNITY_ACTIVITY_WARNING_DAYS": 14,
    "COMMUNITY_ACTIVITY_REQUIRED_INACTIVE_CHECKS": 2,
    "COMMUNITY_ACTIVITY_MIN_CONFIDENCE": 0.85,
    "COMMUNITY_ACTIVITY_BATCH_SIZE": 10,
    "COMMUNITY_ACTIVITY_AUTO_HIDE": False,
    "COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test",
    "DISCORD_REPORT_WEBHOOK_URL": "",
    "DISCORD_WEBHOOK_URL": "",
}


def aware_datetime(year, month, day, hour=3):
    return timezone.make_aware(datetime(year, month, day, hour), timezone.get_current_timezone())


def assessment(
    decision,
    *,
    confidence=0.95,
    signal=None,
    last_activity_at=None,
    evidence=None,
    reason="判定理由",
):
    default_signal = {
        "active": "recent_activity",
        "inactive": "no_recent_activity",
        "uncertain": "insufficient_evidence",
    }[decision]
    return ActivityAssessment(
        decision=decision,
        signal=signal or default_signal,
        confidence=confidence,
        last_activity_at=last_activity_at,
        reason=reason,
        evidence=evidence or [],
        response_id="resp_test",
        model_name="grok-4.5",
        cost_in_usd_ticks=12_000_000,
    )


class FakeClient:
    model = "grok-4.5"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def assess(self, community, *, from_date, to_date):
        self.calls.append((community.pk, from_date, to_date))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@override_settings(**MONITOR_SETTINGS)
class CommunityActivityMonitorTest(TestCase):
    def setUp(self):
        self.community = Community.objects.create(
            name="活動監視テスト集会",
            frequency="毎週",
            status="approved",
            sns_url="https://x.com/activity_test",
            twitter_hashtag="#活動監視テスト",
        )
        self.first_run = aware_datetime(2026, 7, 1)

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_first_inactive_check_warns_but_does_not_hide(self, mock_notify):
        summary = run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run,
            client=FakeClient(assessment("inactive")),
        )

        self.community.refresh_from_db()
        state = CommunityActivityState.objects.get(community=self.community)
        check = CommunityActivityCheck.objects.get(community=self.community)
        self.assertIsNone(self.community.end_at)
        self.assertEqual(state.status, CommunityActivityState.Status.SUSPECTED_INACTIVE)
        self.assertEqual(state.consecutive_inactive_checks, 1)
        self.assertIsNotNone(state.warning_sent_at)
        self.assertEqual(check.action, CommunityActivityCheck.Action.WARNED)
        self.assertEqual(summary["warned"], 1)
        mock_notify.assert_called_once()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_second_inactive_check_after_grace_auto_hides(self, mock_notify):
        state = CommunityActivityState.objects.create(
            community=self.community,
            status=CommunityActivityState.Status.SUSPECTED_INACTIVE,
            consecutive_inactive_checks=1,
            inactive_detected_at=self.first_run,
            warning_sent_at=self.first_run,
            last_checked_at=self.first_run,
        )
        second_run = self.first_run + timedelta(days=15)

        summary = run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=second_run,
            client=FakeClient(assessment("inactive")),
        )

        self.community.refresh_from_db()
        state.refresh_from_db()
        check = CommunityActivityCheck.objects.get(community=self.community)
        self.assertEqual(self.community.end_at, timezone.localdate(second_run))
        self.assertEqual(state.status, CommunityActivityState.Status.INACTIVE)
        self.assertEqual(state.consecutive_inactive_checks, 2)
        self.assertEqual(check.action, CommunityActivityCheck.Action.HIDDEN)
        self.assertEqual(summary["hidden"], 1)
        mock_notify.assert_called_once()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_uncertain_resets_inactive_streak(self, mock_notify):
        state = CommunityActivityState.objects.create(
            community=self.community,
            status=CommunityActivityState.Status.SUSPECTED_INACTIVE,
            consecutive_inactive_checks=1,
            inactive_detected_at=self.first_run,
            warning_sent_at=self.first_run,
        )

        run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run + timedelta(days=15),
            client=FakeClient(assessment("uncertain", confidence=0.5)),
        )

        self.community.refresh_from_db()
        state.refresh_from_db()
        self.assertIsNone(self.community.end_at)
        self.assertEqual(state.status, CommunityActivityState.Status.UNCERTAIN)
        self.assertEqual(state.consecutive_inactive_checks, 0)
        self.assertIsNone(state.warning_sent_at)
        mock_notify.assert_not_called()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_low_confidence_inactive_is_downgraded_to_uncertain(self, mock_notify):
        summary = run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run,
            client=FakeClient(assessment("inactive", confidence=0.7)),
        )

        state = CommunityActivityState.objects.get(community=self.community)
        self.assertEqual(state.status, CommunityActivityState.Status.UNCERTAIN)
        self.assertEqual(summary["uncertain"], 1)
        self.assertEqual(summary["inactive"], 0)
        mock_notify.assert_not_called()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_active_result_clears_previous_warning(self, mock_notify):
        CommunityActivityState.objects.create(
            community=self.community,
            status=CommunityActivityState.Status.SUSPECTED_INACTIVE,
            consecutive_inactive_checks=1,
            inactive_detected_at=self.first_run,
            warning_sent_at=self.first_run,
        )
        active_date = date(2026, 6, 30)

        run_community_activity_checks(
            force=True,
            now=self.first_run,
            client=FakeClient(
                assessment(
                    "active",
                    last_activity_at=active_date,
                    evidence=[
                        {
                            "url": "https://x.com/activity_test/status/1",
                            "posted_at": active_date.isoformat(),
                            "summary": "次回開催告知",
                        }
                    ],
                )
            ),
        )

        state = CommunityActivityState.objects.get(community=self.community)
        self.assertEqual(state.status, CommunityActivityState.Status.ACTIVE)
        self.assertEqual(state.consecutive_inactive_checks, 0)
        self.assertIsNone(state.warning_sent_at)
        mock_notify.assert_not_called()

    @patch("community.activity_monitor.send_activity_notification")
    def test_dry_run_does_not_write_or_notify(self, mock_notify):
        summary = run_community_activity_checks(
            dry_run=True,
            force=True,
            auto_hide=True,
            now=self.first_run,
            client=FakeClient(assessment("inactive")),
        )

        self.community.refresh_from_db()
        self.assertIsNone(self.community.end_at)
        self.assertFalse(CommunityActivityState.objects.exists())
        self.assertFalse(CommunityActivityCheck.objects.exists())
        self.assertEqual(summary["results"][0]["action"], "would_warn")
        mock_notify.assert_not_called()

    @override_settings(COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL="")
    @patch("community.activity_monitor.send_activity_notification", return_value=False)
    def test_missing_or_failed_warning_blocks_auto_hide(self, mock_notify):
        state = CommunityActivityState.objects.create(
            community=self.community,
            status=CommunityActivityState.Status.SUSPECTED_INACTIVE,
            consecutive_inactive_checks=1,
            inactive_detected_at=self.first_run,
            warning_sent_at=None,
        )

        run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run + timedelta(days=30),
            client=FakeClient(assessment("inactive")),
        )

        self.community.refresh_from_db()
        state.refresh_from_db()
        self.assertIsNone(self.community.end_at)
        self.assertIsNone(state.warning_sent_at)
        mock_notify.assert_called_once()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_explicit_end_without_verified_post_is_uncertain(self, mock_notify):
        summary = run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run,
            client=FakeClient(
                assessment(
                    "inactive",
                    signal="explicit_end",
                    evidence=[],
                    reason="終了告知があると推定",
                )
            ),
        )

        state = CommunityActivityState.objects.get(community=self.community)
        self.assertEqual(state.status, CommunityActivityState.Status.UNCERTAIN)
        self.assertEqual(summary["uncertain"], 1)
        mock_notify.assert_not_called()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_no_recent_activity_without_official_x_account_is_uncertain(self, mock_notify):
        self.community.sns_url = ""
        self.community.save(update_fields=["sns_url"])

        summary = run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run,
            client=FakeClient(assessment("inactive")),
        )

        state = CommunityActivityState.objects.get(community=self.community)
        self.assertEqual(state.status, CommunityActivityState.Status.UNCERTAIN)
        self.assertEqual(summary["uncertain"], 1)
        mock_notify.assert_not_called()

    @patch("community.activity_monitor.send_activity_notification", return_value=True)
    def test_irregular_community_is_not_hidden_for_silence(self, mock_notify):
        self.community.frequency = "不定期"
        self.community.save(update_fields=["frequency"])

        summary = run_community_activity_checks(
            force=True,
            auto_hide=True,
            now=self.first_run,
            client=FakeClient(assessment("inactive")),
        )

        state = CommunityActivityState.objects.get(community=self.community)
        self.assertEqual(state.status, CommunityActivityState.Status.UNCERTAIN)
        self.assertEqual(summary["uncertain"], 1)
        mock_notify.assert_not_called()

    def test_api_error_is_recorded_and_batch_continues(self):
        summary = run_community_activity_checks(
            force=True,
            now=self.first_run,
            client=FakeClient(RuntimeError("xAI unavailable")),
        )

        state = CommunityActivityState.objects.get(community=self.community)
        check = CommunityActivityCheck.objects.get(community=self.community)
        self.assertEqual(state.status, CommunityActivityState.Status.ERROR)
        self.assertIsNone(state.check_started_at)
        self.assertEqual(check.result, CommunityActivityCheck.Result.ERROR)
        self.assertEqual(summary["errors"], 1)


@override_settings(**MONITOR_SETTINGS, REQUEST_TOKEN="request-token")
class ActivityMonitorEndpointTest(TestCase):
    def test_rejects_invalid_token(self):
        response = self.client.post(reverse("community:activity_monitor_run"))
        self.assertEqual(response.status_code, 401)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("community:activity_monitor_run"))
        self.assertEqual(response.status_code, 405)

    @patch("community.views.activity_monitor.run_community_activity_checks")
    def test_runs_with_request_token(self, mock_run):
        mock_run.return_value = {"processed": 0, "errors": 0}
        response = self.client.post(
            reverse("community:activity_monitor_run") + "?dry_run=true&limit=5",
            HTTP_REQUEST_TOKEN="request-token",
        )
        self.assertEqual(response.status_code, 200)
        mock_run.assert_called_once_with(
            dry_run=True,
            force=False,
            limit=5,
            community_id=None,
            auto_hide=None,
        )

    @override_settings(XAI_API_KEY="")
    def test_returns_503_without_xai_key(self):
        response = self.client.post(
            reverse("community:activity_monitor_run"),
            HTTP_REQUEST_TOKEN="request-token",
        )
        self.assertEqual(response.status_code, 503)


class XaiActivityClientTest(TestCase):
    def test_extract_x_handle(self):
        self.assertEqual(extract_x_handle("https://x.com/example_vrc"), "example_vrc")
        self.assertEqual(extract_x_handle("https://twitter.com/example_vrc/status/1"), "example_vrc")
        self.assertEqual(extract_x_handle("https://x.com/home"), "")
        self.assertEqual(extract_x_handle("https://example.com/example_vrc"), "")

    def test_parses_structured_output_and_verified_citation(self):
        community = MagicMock(
            pk=1,
            name="テスト集会",
            organizers="主催者",
            frequency="毎週",
            sns_url="https://x.com/test_event",
            hashtag_names=["test_event"],
            group_url="https://vrc.group/TEST.1234",
        )
        response_payload = {
            "id": "resp_123",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "decision": "active",
                                    "signal": "recent_activity",
                                    "confidence": 0.98,
                                    "last_activity_at": "2026-07-10",
                                    "reason": "公式アカウントが次回開催を告知",
                                    "evidence": [
                                        {
                                            "url": "https://twitter.com/test_event/status/123?ref=x",
                                            "posted_at": "2026-07-10",
                                            "summary": "次回開催告知",
                                        }
                                    ],
                                }
                            ),
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://x.com/test_event/status/123",
                                }
                            ],
                        }
                    ],
                }
            ],
            "usage": {"cost_in_usd_ticks": 12345},
        }
        http_client = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = response_payload
        http_client.post.return_value = response
        client = XaiActivityClient(
            api_key="xai-test",
            model="grok-4.5",
            timeout_seconds=30,
            http_client=http_client,
        )

        result = client.assess(
            community,
            from_date=date(2026, 4, 21),
            to_date=date(2026, 7, 20),
        )

        self.assertEqual(result.decision, "active")
        self.assertEqual(result.evidence[0]["url"], "https://x.com/i/status/123")
        self.assertEqual(result.cost_in_usd_ticks, 12345)
        request_json = http_client.post.call_args.kwargs["json"]
        self.assertFalse(request_json["store"])
        self.assertEqual(request_json["include"], ["no_inline_citations"])
        self.assertEqual(request_json["prompt_cache_key"], "vrc-ta-hub-community-activity-v1")
        self.assertEqual(request_json["tools"][0]["type"], "x_search")
        self.assertEqual(request_json["tools"][0]["from_date"], "2026-04-21")
        self.assertEqual(request_json["text"]["format"]["type"], "json_schema")

    def test_active_without_verified_evidence_becomes_uncertain(self):
        result = normalize_assessment(
            assessment(
                "active",
                last_activity_at=date(2026, 7, 10),
                evidence=[],
            ),
            from_date=date(2026, 4, 21),
            to_date=date(2026, 7, 20),
        )
        self.assertEqual(result.decision, "uncertain")
        self.assertEqual(result.signal, "insufficient_evidence")

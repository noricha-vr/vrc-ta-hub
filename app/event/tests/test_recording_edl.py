"""EDL v1 スキーマ検証と録画レビュー・確定キューモデルの単体テスト。"""
from __future__ import annotations

import copy
import math

from django.db import IntegrityError, transaction
from django.test import TestCase

from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.edl import (
    EDLValidationError,
    extract_adopted_duration_sec,
    validate_edl_v1,
)
from tests.factories import make_community, make_event, make_event_detail


def _valid_edl(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 1200.0},
        "trim": {"start_sec": 30.0, "end_sec": 930.0},
        "boundary_candidates": {
            "start": [{"sec": 30.0, "label": "挨拶開始"}],
            "end": [930.0],
        },
        "cuts": [],
    }
    payload.update(overrides)
    return payload


class EDLv1ValidationTest(TestCase):
    def test_valid_minimal_edl_passes_and_returns_isolated_copy(self):
        edl = _valid_edl()
        del edl["boundary_candidates"]

        validated = validate_edl_v1(edl)
        validated["trim"]["start_sec"] = 99.0

        self.assertEqual(edl["trim"]["start_sec"], 30.0)
        self.assertAlmostEqual(extract_adopted_duration_sec(_valid_edl()), 900.0)

    def test_valid_edl_with_accept_pending_reject_cuts(self):
        edl = _valid_edl(
            cuts=[
                {
                    "id": "cut-1",
                    "start_sec": 100.0,
                    "end_sec": 120.0,
                    "kind": "silence",
                    "label": "無音1",
                    "source": "vad",
                    "confidence": 0.95,
                    "action": "accept",
                },
                {
                    # accept と重なる pending/reject は許容される
                    "id": "cut-2",
                    "start_sec": 110.0,
                    "end_sec": 130.0,
                    "kind": "filler",
                    "label": "候補",
                    "action": "pending",
                },
                {
                    "id": "cut-3",
                    "start_sec": 115.0,
                    "end_sec": 125.0,
                    "kind": "filler",
                    "label": "却下候補",
                    "action": "reject",
                },
                {
                    "id": "cut-4",
                    "start_sec": 200.0,
                    "end_sec": 230.0,
                    "kind": "pause",
                    "label": "休憩",
                    "action": "accept",
                },
            ]
        )

        validated = validate_edl_v1(edl)

        self.assertEqual(len(validated["cuts"]), 4)
        self.assertAlmostEqual(extract_adopted_duration_sec(validated), 850.0)

    def test_rejects_invalid_schema_version_and_types(self):
        for bad in (None, [], "edl", _valid_edl(schema_version=2), _valid_edl(schema_version=True), _valid_edl(schema_version=1.0)):
            with self.subTest(bad=bad), self.assertRaises(EDLValidationError):
                validate_edl_v1(bad)

        # 巨大整数・unhashable action でも TypeError/OverflowError にせず EDLValidationError に変換する
        for bad_payload in (
            _valid_edl(safe_bounds={"min_sec": 0.0, "max_sec": 10**400}),
            _valid_edl(
                cuts=[
                    {
                        "id": "c1",
                        "start_sec": 40.0,
                        "end_sec": 50.0,
                        "kind": "silence",
                        "label": "無音",
                        "action": [],
                    }
                ]
            ),
            _valid_edl(
                cuts=[
                    {
                        "id": "c1",
                        "start_sec": 40.0,
                        "end_sec": 50.0,
                        "kind": "silence",
                        "label": "無音",
                        "action": {},
                    }
                ]
            ),
        ):
            with self.subTest(bad_payload=bad_payload), self.assertRaises(EDLValidationError):
                validate_edl_v1(bad_payload)

        missing_cuts = _valid_edl()
        del missing_cuts["cuts"]
        with self.assertRaises(EDLValidationError):
            validate_edl_v1(missing_cuts)

        with self.assertRaises(EDLValidationError):
            validate_edl_v1(_valid_edl(unexpected="value"))

    def test_rejects_nan_inf_and_booleans(self):
        cases = [
            _valid_edl(safe_bounds={"min_sec": 0.0, "max_sec": math.nan}),
            _valid_edl(safe_bounds={"min_sec": 0.0, "max_sec": math.inf}),
            _valid_edl(trim={"start_sec": -math.inf, "end_sec": 100.0}),
            _valid_edl(trim={"start_sec": False, "end_sec": True}),
            _valid_edl(
                cuts=[
                    {
                        "id": "c1",
                        "start_sec": 40.0,
                        "end_sec": 50.0,
                        "kind": "silence",
                        "label": "無音",
                        "confidence": math.nan,
                        "action": "pending",
                    }
                ]
            ),
        ]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(EDLValidationError):
                validate_edl_v1(bad)

    def test_rejects_invalid_safe_bounds_and_trim(self):
        cases = [
            _valid_edl(safe_bounds={"min_sec": -1.0, "max_sec": 100.0}),
            _valid_edl(safe_bounds={"min_sec": 100.0, "max_sec": 100.0}),
            _valid_edl(safe_bounds={"min_sec": 200.0, "max_sec": 100.0}),
            _valid_edl(trim={"start_sec": 10.0, "end_sec": 100.0}, safe_bounds={"min_sec": 20.0, "max_sec": 200.0}),
            _valid_edl(trim={"start_sec": 30.0, "end_sec": 1201.0}),
            _valid_edl(trim={"start_sec": 100.0, "end_sec": 100.0}),
            _valid_edl(trim={"start_sec": 200.0, "end_sec": 100.0}),
        ]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(EDLValidationError):
                validate_edl_v1(bad)

    def test_rejects_invalid_boundary_candidates(self):
        cases = [
            _valid_edl(boundary_candidates=[]),
            _valid_edl(boundary_candidates={"other": []}),
            _valid_edl(boundary_candidates={"start": "not-list"}),
            _valid_edl(boundary_candidates={"end": [-1.0]}),
            _valid_edl(boundary_candidates={"start": [{"sec": math.inf}]}),
        ]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(EDLValidationError):
                validate_edl_v1(bad)

    def test_rejects_invalid_cuts_and_accept_overlap_touch_reverse_or_zero_duration(self):
        base_cut = {
            "id": "c1",
            "start_sec": 50.0,
            "end_sec": 60.0,
            "kind": "silence",
            "label": "無音",
            "action": "accept",
        }
        second_cut = copy.deepcopy(base_cut)
        second_cut["id"] = "c2"

        # 範囲外・逆転・重複ID・不正action
        out_of_trim = {**base_cut, "start_sec": 10.0}
        reversed_cut = {**base_cut, "start_sec": 70.0, "end_sec": 60.0}
        blank_id = {**base_cut, "id": "   "}
        bad_action = {**base_cut, "action": "delete"}

        for cuts in (
            [out_of_trim],
            [reversed_cut],
            [blank_id],
            [bad_action],
            [base_cut, {**base_cut}],  # ID重複
            [base_cut, {**second_cut, "start_sec": 55.0, "end_sec": 70.0}],  # accept重複
            [base_cut, {**second_cut, "start_sec": 60.0, "end_sec": 70.0}],  # accept接触
            [{**base_cut, "start_sec": 100.0, "end_sec": 120.0}, {**second_cut, "start_sec": 40.0, "end_sec": 50.0}],  # 順逆転
            [{**base_cut, "start_sec": 30.0, "end_sec": 930.0}],  # 採用尺 0 秒
        ):
            with self.subTest(cuts=cuts), self.assertRaises(EDLValidationError):
                validate_edl_v1(_valid_edl(cuts=cuts))

    def test_rejects_zero_adopted_duration_with_decimal_edge_floats(self):
        edl = _valid_edl(
            safe_bounds={"min_sec": 0.0, "max_sec": 1.0},
            trim={"start_sec": 0.0, "end_sec": 0.3},
            cuts=[
                {
                    "id": "c1",
                    "start_sec": 0.0,
                    "end_sec": 0.3,
                    "kind": "silence",
                    "label": "全カット",
                    "action": "accept",
                }
            ],
        )
        with self.assertRaises(EDLValidationError):
            validate_edl_v1(edl)


class RecordingModelsTest(TestCase):
    def setUp(self):
        self.community = make_community(name="録画テスト集会", recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(self.event, status="approved")

    def test_is_public_locked_property(self):
        item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
        )
        self.assertFalse(item.is_public_locked)

        item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        item.save(update_fields=["review_status"])
        self.assertTrue(item.is_public_locked)
        self.assertIsNotNone(item.public_locked_at)

        # 公開停止で private に遷移しても公開ロック履歴は保持される
        item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        item.save(update_fields=["review_status", "youtube_draft_privacy"])
        self.assertTrue(item.is_public_locked)

    def test_active_lock_key_enforces_single_active_order_and_allows_multiple_terminal(self):
        item = RecordingReviewItem.objects.create(event_detail=self.detail, edl_json=_valid_edl())
        first = RecordingEditOrder.objects.create(
            review_item=item,
            event_detail=self.detail,
            base_revision=1,
            target_revision=2,
            status=RecordingEditOrder.Status.QUEUED,
        )
        self.assertEqual(first.active_lock_key, f"item:{item.pk}")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RecordingEditOrder.objects.create(
                    review_item=item,
                    event_detail=self.detail,
                    base_revision=1,
                    target_revision=2,
                    status=RecordingEditOrder.Status.QUEUED,
                )

        first.status = RecordingEditOrder.Status.SUCCEEDED
        first.save(update_fields=["status"])
        self.assertIsNone(first.active_lock_key)

        second = RecordingEditOrder.objects.create(
            review_item=item,
            event_detail=self.detail,
            base_revision=2,
            target_revision=3,
            status=RecordingEditOrder.Status.FAILED,
        )
        self.assertIsNone(second.active_lock_key)

        third = RecordingEditOrder.objects.create(
            review_item=item,
            event_detail=self.detail,
            base_revision=2,
            target_revision=3,
            status=RecordingEditOrder.Status.QUEUED,
        )
        self.assertEqual(third.active_lock_key, f"item:{item.pk}")

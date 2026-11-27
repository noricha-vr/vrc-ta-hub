"""E2E 検証用のコミュニティ・イベント・発表を冪等にシードする管理コマンド。"""
from __future__ import annotations

import json
from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from community.constants import RecordingPolicy
from community.models import Community
from django.contrib.auth import get_user_model
from django.db import transaction
from event.models import Event, EventDetail, RecordingEditOrder, RecordingReviewItem
from user_account.models import APIKey

E2E_COMMUNITY_PREFIX = "E2E "


class Command(BaseCommand):
    help = "E2E 検証用のダミー集会・イベント・発表（approved/public/LT）と E2E API キーをシードし JSON で ID を返す。"

    def add_arguments(self, parser):
        parser.add_argument("--community-name", default="E2E 検証集会")
        parser.add_argument("--theme", default="E2E 自動録画テスト発表")
        parser.add_argument("--speaker", default="E2E 登壇者")
        parser.add_argument("--api-key", default="e2e-recording-ops-token")
        parser.add_argument("--reset", action="store_true", help="既存の同名 E2E コミュニティ配下を初期化してから作成")

    def handle(self, *args, **options):
        from .e2e_check_isolation import validate_e2e_isolation

        iso = validate_e2e_isolation()
        if not iso["ok"]:
            self.stderr.write(json.dumps(iso, ensure_ascii=False))
            raise SystemExit(2)

        community_name = str(options["community_name"]).strip()
        if not community_name.startswith(E2E_COMMUNITY_PREFIX):
            self.stderr.write(
                json.dumps({"ok": False, "error": f"community_name must start with {E2E_COMMUNITY_PREFIX!r}"}, ensure_ascii=False)
            )
            raise SystemExit(2)

        with transaction.atomic():
            User = get_user_model()
            staff_user, _ = User.objects.get_or_create(
                user_name="e2e_staff",
                defaults={"email": "e2e-staff@example.invalid", "is_staff": True, "is_superuser": True, "is_active": True},
            )
            raw_api_key = str(options["api_key"]).strip()
            if raw_api_key:
                APIKey.objects.update_or_create(
                    key=APIKey.hash_raw_key(raw_api_key),
                    defaults={"user": staff_user, "name": "e2e-harness", "is_active": True, "scope": APIKey.SCOPE_WRITE},
                )

            if options["reset"]:
                existing = Community.objects.filter(name=community_name)
                detail_ids = list(EventDetail.all_objects.filter(event__community__in=existing).values_list("pk", flat=True))
                RecordingEditOrder.objects.filter(event_detail_id__in=detail_ids).delete()
                RecordingReviewItem.objects.filter(event_detail_id__in=detail_ids).delete()
                EventDetail.all_objects.filter(pk__in=detail_ids).delete()
                Event.objects.filter(community__in=existing).delete()

            community, _ = Community.objects.update_or_create(
                name=community_name,
                defaults={
                    "status": "approved",
                    "recording_allowed": True,
                    "default_recording_policy": RecordingPolicy.PUBLIC,
                    "start_time": time(21, 0),
                    "duration": 60,
                    "weekdays": ["Sat"],
                    "frequency": "Weekly",
                    "organizers": "E2E Organizer",
                    "description": "E2E automated verification community",
                },
            )
            today = timezone.localdate()
            event, _ = Event.objects.get_or_create(
                community=community,
                date=today,
                defaults={
                    "start_time": time(21, 0),
                    "duration": 60,
                    "weekday": today.strftime("%a"),
                },
            )
            detail, _ = EventDetail.objects.update_or_create(
                event=event,
                theme=options["theme"],
                speaker=options["speaker"],
                defaults={
                    "detail_type": "LT",
                    "status": "approved",
                    "recording_policy": RecordingPolicy.PUBLIC,
                    "article_consent": EventDetail.ArticleConsent.OK,
                    "start_time": time(21, 0),
                    "duration": 15,
                },
            )
        payload = {
            "community_id": community.pk,
            "event_id": event.pk,
            "event_detail_id": detail.pk,
            "hub_sha": iso["hub_sha"],
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=False))

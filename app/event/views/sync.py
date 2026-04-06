from datetime import date, datetime
from typing import Dict, List

from community.models import Community
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

from event.models import Event
from website.settings import REQUEST_TOKEN

from ..sync_to_google import DatabaseToGoogleSync
from .compat import get_logger


def sync_calendar_events(request):
    """データベースからGoogleカレンダーへの同期（重複防止機能付き）"""
    if request.method != "GET":
        return HttpResponse("Invalid request method.", status=405)

    request_token = request.headers.get("Request-Token", "")
    if request_token != REQUEST_TOKEN:
        return HttpResponse("Unauthorized", status=401)

    logger = get_logger()
    try:
        logger.info("=" * 80)
        logger.info("データベースからGoogleカレンダーへの同期開始")
        logger.info(f"同期開始時刻: {timezone.now()}")
        logger.info("=" * 80)

        sync = DatabaseToGoogleSync()
        stats = sync.sync_all_communities(months_ahead=3)

        logger.info("=" * 80)
        logger.info("同期完了サマリー:")
        logger.info(f"  現在のDBイベント総数: {Event.objects.count()}件")
        logger.info(f"  新規作成: {stats['created']}件")
        logger.info(f"  更新: {stats['updated']}件")
        logger.info(f"  削除: {stats['deleted']}件")
        logger.info(f"  エラー: {stats['errors']}件")

        if stats.get("duplicate_prevented", 0) > 0:
            logger.info(
                f"  重複防止により更新に切り替え: {stats['duplicate_prevented']}件"
            )

        logger.info(f"同期終了時刻: {timezone.now()}")
        logger.info("=" * 80)

        response_message = (
            "Calendar events synchronized successfully. "
            f"Created: {stats['created']}, Updated: {stats['updated']}, "
            f"Skipped: {stats.get('skipped', 0)}, "
            f"Deleted: {stats['deleted']}, Errors: {stats['errors']}"
        )

        if stats.get("duplicate_prevented", 0) > 0:
            response_message += (
                f", Duplicate prevented: {stats['duplicate_prevented']}"
            )

        return HttpResponse(response_message, status=200)
    except Exception as exc:
        logger.error("=" * 80)
        logger.error(f"同期失敗: {str(exc)}")
        logger.error("=" * 80, exc_info=True)
        return HttpResponse(
            "Failed to sync calendar events. Check server logs for details.",
            status=500,
        )


def delete_outdated_events(calendar_events: List[Dict], today: date) -> None:
    """
    DBに登録されているイベントのうち、Googleカレンダーに存在しないものを削除する

    このメソッドが必要な理由:
    1. データの整合性維持: GoogleカレンダーとDBのイベントデータを
       同期させ、システム全体でのデータの一貫性を保ちます。
    2. 不要データの削除: キャンセルされたイベントや終了したイベントを
       適切に削除し、DBの肥大化を防ぎます。
    3. ユーザー体験の向上: 過去のイベントや無効なイベントを表示から
       除外することで、ユーザーに適切な情報のみを提供します。
    """
    logger = get_logger()
    future_events = Event.objects.filter(date__gte=today).values(
        "id", "community__name", "date", "start_time"
    )

    for db_event in future_events:
        db_event_naive = datetime.combine(db_event["date"], db_event["start_time"])
        db_event_datetime = timezone.make_aware(
            db_event_naive, timezone.get_current_timezone()
        )
        db_event_str = (
            f"{db_event_datetime.isoformat()} {db_event['community__name']}"
        )

        found = False
        for event in calendar_events:
            calendar_start_str = event["start"].get(
                "dateTime", event["start"].get("date")
            )
            try:
                calendar_start = datetime.strptime(
                    calendar_start_str, "%Y-%m-%dT%H:%M:%S%z"
                )
                calendar_start_local = calendar_start.astimezone(
                    timezone.get_current_timezone()
                )

                same_date = calendar_start_local.date() == db_event["date"]
                same_time = (
                    calendar_start_local.hour == db_event["start_time"].hour
                    and calendar_start_local.minute == db_event["start_time"].minute
                )
                same_name = event["summary"].strip() == db_event["community__name"]

                if same_date and same_time and same_name:
                    found = True
                    logger.info(
                        "イベント一致確認: "
                        f"DB={db_event_str}, "
                        f"Calendar={calendar_start_local.isoformat()} {event['summary']}"
                    )
                    break
            except Exception as parsing_err:
                logger.warning(
                    f"カレンダーイベント解析エラー: {str(parsing_err)} - {calendar_start_str}"
                )
                continue

        if not found:
            logger.warning(
                f"削除対象イベント: {db_event_str} - カレンダーに存在しないため削除します"
            )
            Event.objects.filter(id=db_event["id"]).delete()
            logger.info(f"Event deleted: {db_event_str}")
        else:
            logger.info(
                f"イベント保持: {db_event_str} - カレンダーに存在するため保持します"
            )


def register_calendar_events(calendar_events: List[Dict]) -> None:
    """
    Googleカレンダーのイベントをデータベースに登録する

    このメソッドが必要な理由:
    1. イベント情報の集中管理: Googleカレンダーのイベント情報を
       システムのDBに取り込み、一元管理を実現します。
    2. イベント情報の同期: 新規イベントや更新されたイベント情報を
       システムに反映し、最新の情報を維持します。
    3. コミュニティ活動の可視化: VRChatコミュニティの活動を
       システム上で可視化し、ユーザーの参加を促進します。
    """
    logger = get_logger()
    for event in calendar_events:
        start_datetime = event["start"].get("dateTime", event["start"].get("date"))
        end_datetime = event["end"].get("dateTime", event["end"].get("date"))
        summary = event["summary"].strip()

        start = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M:%S%z")
        end = datetime.strptime(end_datetime, "%Y-%m-%dT%H:%M:%S%z")

        current_tz = timezone.get_current_timezone()
        start_local = start.astimezone(current_tz)
        end_local = end.astimezone(current_tz)

        community = Community.objects.filter(name=summary).first()
        if not community:
            logger.warning(f"Community not found: {summary}")
            continue

        event_str = f"{start_local} - {end_local} {summary}"
        logger.info(f"Event: {event_str}")

        existing_event = Event.objects.filter(
            community=community,
            date=start_local.date(),
            start_time=start_local.time(),
        ).first()

        if existing_event:
            if (
                existing_event.duration
                != (end_local - start_local).total_seconds() // 60
                or existing_event.google_calendar_event_id != event["id"]
            ):
                existing_event.duration = (
                    end_local - start_local
                ).total_seconds() // 60
                existing_event.google_calendar_event_id = event["id"]
                existing_event.save()

                cache_key = f"calendar_entry_url_{existing_event.id}"
                cache.delete(cache_key)

                logger.info(f"Event updated: {event_str}")
        else:
            Event.objects.create(
                community=community,
                date=start_local.date(),
                start_time=start_local.time(),
                duration=(end_local - start_local).total_seconds() // 60,
                weekday=start_local.strftime("%a"),
                google_calendar_event_id=event["id"],
            )
            logger.info(f"Event created: {event_str}")

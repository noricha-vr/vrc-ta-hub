import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import UpdateView, DeleteView

from event.forms import EventDateUpdateForm, EventUpdateForm
from event.models import Event, EventDetail
from event.services.recurrence_override import (
    delete_event_with_tombstones,
    get_cascade_occurrences,
    move_event_occurrence,
)
from event.sync_to_google import build_google_event_description
from event_calendar.calendar_utils import generate_google_calendar_url
from ta_hub.index_cache import clear_index_view_cache
from utils.vrchat_time import get_vrchat_today
from website.settings import GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID
from event.google_calendar import GoogleCalendarService

logger = logging.getLogger(__name__)


class EventDateUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """イベントの開始時刻を保ったまま開催日だけを変更する。"""

    model = Event
    form_class = EventDateUpdateForm
    template_name = 'event/date_form.html'
    success_url = reverse_lazy('event:my_list')

    def test_func(self):
        event = self.get_object()
        return (
            self.request.user.is_superuser
            or event.community.can_edit(self.request.user)
        )

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'このイベントを編集する権限がありません。')
            return redirect('event:my_list')
        return super().handle_no_permission()

    def dispatch(self, request, *args, **kwargs):
        # 過去イベントの変更はブロック（URL 直叩き対策）。
        # 判定基準は EventUpdateView.dispatch と my_list の _attach_edit_flags に揃える。
        if request.user.is_authenticated:
            event = get_object_or_404(Event, pk=kwargs.get('pk'))
            if event.date < get_vrchat_today():
                messages.error(request, '過去のイベントは開催日を変更できません。')
                return redirect('event:my_list')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        """ModelFormがinstanceを書き換える前の開催日を保持する。"""
        self.original_date = self.object.date
        self.original_weekday = self.object.weekday
        return super().get_form_kwargs()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_recurring_child'] = (
            self.object.recurring_master_id is not None
        )
        context['is_recurring_master'] = self.object.is_recurring_master
        return context

    def form_valid(self, form):
        event = self.object
        new_date = form.cleaned_data['date']
        if new_date == self.original_date:
            messages.info(self.request, '開催日は変更されていません。')
            return redirect(self.success_url)

        self._restore_original_schedule(event)
        lock_message = self._get_vket_lock_message(event, new_date)
        if lock_message:
            form.add_error(None, lock_message)
            return self.form_invalid(form)

        move_event_occurrence(event, new_date)
        if event.google_calendar_event_id:
            try:
                self._update_google_calendar_event(event, new_date)
            except Exception:
                logger.exception(
                    'Google Calendarの日付更新に失敗: event_id=%s',
                    event.pk,
                )
                messages.warning(
                    self.request,
                    '開催日は変更しましたが、Googleカレンダーの更新に失敗しました。'
                    '後続の同期で再反映します。',
                )

        messages.success(self.request, 'イベントの開催日を変更しました。')
        return redirect(self.success_url)

    def _restore_original_schedule(self, event: Event) -> None:
        """ModelFormが未保存instanceへ反映した日付を変更前へ戻す。"""
        event.date = self.original_date
        event.weekday = self.original_weekday

    def _get_vket_lock_message(self, event: Event, new_date) -> str:
        """変更前後のどちらかがVketロック対象ならメッセージを返す。"""
        if self.request.user.is_superuser or self.request.user.is_staff:
            return ''

        from vket.services import get_vket_lock_info

        old_locked, old_message = get_vket_lock_info(event)
        new_locked, new_message = get_vket_lock_info(event, date=new_date)
        if old_locked or new_locked:
            return old_message or new_message
        return ''

    @staticmethod
    def _update_google_calendar_event(event: Event, new_date) -> None:
        start_at = datetime.combine(new_date, event.start_time)
        start_at = timezone.make_aware(
            start_at,
            timezone.get_current_timezone(),
        )
        calendar_service = GoogleCalendarService(
            calendar_id=GOOGLE_CALENDAR_ID,
            credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
        )
        calendar_service.update_event(
            event_id=event.google_calendar_event_id,
            start_time=start_at,
            end_time=start_at + timedelta(minutes=event.duration),
            # description も更新する（本文内「開催日時」が旧日付のまま残ると、
            # 後続の DB→Google 同期が日時+ID 一致で skipped 判定になり恒久的に矛盾する）。
            # sync 側と同じ生成関数を使って文面のドリフトを防ぐ。EventUpdateView と同規約。
            description=build_google_event_description(event),
        )


class EventUpdateView(LoginRequiredMixin, UpdateView):
    """イベントの開始時刻のみを編集するビュー。

    date/duration などは変更不可。編集を許すのは所属集会の管理者（owner/staff）と
    superuser のみ。Vket コラボ期間中はロックする（superuser/is_staff は免除）。
    """

    model = Event
    form_class = EventUpdateForm
    template_name = 'event/event_form.html'
    success_url = reverse_lazy('event:my_list')

    def dispatch(self, request, *args, **kwargs):
        # 認証は LoginRequiredMixin が先に処理する
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        event = get_object_or_404(Event, pk=kwargs.get('pk'))

        # 権限チェック（superuser は許可）
        if not (request.user.is_superuser or event.community.can_edit(request.user)):
            messages.error(request, "このイベントを編集する権限がありません。")
            return redirect('event:my_list')

        # 過去イベントの編集はブロック（UI で鉛筆非表示のため URL 直叩き対策）。
        # my_list の _attach_edit_flags と同じ VRChat today 基準に揃える。
        if event.date < get_vrchat_today():
            messages.error(request, "過去のイベントは編集できません。")
            return redirect('event:my_list')

        # Vket ロック（superuser/is_staff は免除）
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked, message = get_vket_lock_info(event)
            if locked:
                messages.error(request, message)
                return redirect('event:my_list')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.object
        return context

    def form_valid(self, form):
        # 旧 start_time を退避（EventDetail の delta シフトに使用）
        old_event = Event.objects.get(pk=self.object.pk)
        old_start_time = old_event.start_time
        new_start_time = form.cleaned_data['start_time']

        time_changed = new_start_time != old_start_time

        try:
            with transaction.atomic():
                self.object = form.save()

                # 時刻が変わらない保存では副作用（詳細シフト・キャッシュ無効化・
                # TweetQueue 再生成・GCal patch）を一切走らせない
                if time_changed:
                    # 旧→新 delta で配下 EventDetail の start_time を同量シフト
                    old_dt = datetime.combine(self.object.date, old_start_time)
                    new_dt = datetime.combine(self.object.date, new_start_time)
                    delta = new_dt - old_dt
                    # soft delete 除外は EventDetail.objects の既定挙動（EventDetailManager）
                    for detail in EventDetail.objects.filter(event=self.object):
                        detail_dt = datetime.combine(self.object.date, detail.start_time) + delta
                        # 日跨ぎになる場合は time 部分のみ採用（日付は event.date のまま維持）
                        detail.start_time = detail_dt.time()
                        detail.save(update_fields=['start_time', 'updated_at'])

                    # キャッシュ無効化
                    cache.delete(f'google_calendar_url_{self.object.id}')
                    # VRCイベントカレンダー投稿URLは start_time を埋め込むため両バリアントを消す
                    cache.delete(f'calendar_entry_url_{self.object.id}_True')
                    cache.delete(f'calendar_entry_url_{self.object.id}_False')
                    # lru_cache のクリア（request, event キーで cache されている可能性）
                    generate_google_calendar_url.cache_clear()
                    # IndexView のキャッシュキーは get_vrchat_today() ベース。
                    # event.date を渡すと別キーを消してしまい実キャッシュが残るため引数なしで呼ぶ。
                    clear_index_view_cache()

                    # 未投稿の TweetQueue は generated_text に旧時刻が焼き込まれているため、
                    # 再生成対象に戻す（status='generation_failed' + generated_text='' で
                    # 既存の retry_generation フローが拾って新時刻で再生成する。scheduled_at は
                    # 日付基準のため変更不要）。posted / failed / skipped は触らない。
                    from twitter.models import TweetQueue
                    TweetQueue.objects.filter(
                        event=self.object,
                        status__in=('generating', 'generation_failed', 'ready'),
                    ).update(
                        status='generation_failed',
                        generated_text='',
                        error_message='開始時刻変更により再生成',
                        # in-flight の非同期生成が旧時刻スナップショットで ready に戻すのを防ぐ
                        # （write-back は generation_token の compare-and-set で守られている）
                        generation_token='',
                    )
        except IntegrityError:
            # UniqueConstraint (community, date, start_time) 競合をフォームエラーへ
            form.add_error('start_time', '同じ日時にすでにイベントが登録されています。')
            return self.form_invalid(form)

        # atomic を抜けた後で Google カレンダーを patch（DB はコミット済み）
        if time_changed and self.object.google_calendar_event_id:
            try:
                start_datetime = datetime.combine(self.object.date, self.object.start_time)
                tz = timezone.get_current_timezone()
                start_datetime = timezone.make_aware(start_datetime, tz)
                end_datetime = start_datetime + timedelta(minutes=self.object.duration)
                calendar_service = GoogleCalendarService(
                    calendar_id=GOOGLE_CALENDAR_ID,
                    credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
                )
                # description も更新する（本文内「開催日時」が旧時刻のまま残ると、
                # 後続の DB→Google 同期が日時+ID 一致で skipped 判定になり恒久的に矛盾する）。
                # sync 側と同じ生成関数を使うことで文面のドリフトを防ぐ。summary は community 名で
                # 不変のため渡さない。
                calendar_service.update_event(
                    event_id=self.object.google_calendar_event_id,
                    start_time=start_datetime,
                    end_time=end_datetime,
                    description=build_google_event_description(self.object),
                )
            except Exception:
                # silent failure: DB 保存は成功扱いのまま進めるため、Sentry で連発検知
                # できるよう is_silent=True の構造化ログに揃える（同ファイルの記事生成失敗と同規約）。
                logger.exception(
                    "silent_failure",
                    extra={
                        "event_type": "google_calendar_update_failed",
                        "target_event_id": self.object.id,
                        "is_silent": True,
                    },
                )
                messages.error(
                    self.request,
                    "Googleカレンダーの更新に失敗しました（変更自体は保存済み。"
                    "次回同期時に自動反映されます）",
                )
                return redirect(self.get_success_url())

        if time_changed:
            messages.success(self.request, "開始時刻を変更しました。")
        else:
            messages.info(self.request, "開始時刻に変更はありません。")
        return redirect(self.get_success_url())


class EventDeleteView(LoginRequiredMixin, DeleteView):
    model = Event
    success_url = reverse_lazy('event:my_list')

    def post(self, request, *args, **kwargs):
        event = self.get_object()

        # Vketコラボ期間中は運営調整済みの開催日を守るため、主催者によるイベント削除をブロックする
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked, message = get_vket_lock_info(event)
            if locked:
                messages.error(request, message)
                return redirect('event:my_list')

        # イベントが属する集会に対する削除権限をチェック（主催者のみ）
        if not event.community.can_delete(request.user):
            messages.error(request, "このイベントを削除する権限がありません。")
            return redirect('event:my_list')

        # 削除対象のコミュニティを取得
        user_community = event.community

        logger.info(
            f"イベント削除開始: ID={event.id}, コミュニティ={event.community.name}, 日付={event.date}, 開始時間={event.start_time}")
        logger.info(f"Google Calendar Event ID: {event.google_calendar_event_id}")

        # 以降のイベントも削除するかどうかのチェック
        delete_subsequent = request.POST.get('delete_subsequent') == 'on'
        events_to_delete = [event]

        if delete_subsequent:
            # 同じコミュニティの、選択したイベント以降のイベントを取得
            # ユーザーのコミュニティのイベントのみに制限
            subsequent_events = Event.objects.filter(
                community=user_community,
                date__gt=event.date
            ).order_by('date', 'start_time')
            events_to_delete.extend(subsequent_events)
            logger.info(f"以降のイベントも削除します: {len(subsequent_events)}件")

        # 「以降のイベントも削除」選択時も、Vketコラボ期間中のイベントは運営調整済みのため対象から除外する
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked_events = []
            lock_message = ""
            for evt in events_to_delete:
                locked, message = get_vket_lock_info(evt)
                if locked:
                    locked_events.append(evt)
                    lock_message = message
            if locked_events:
                events_to_delete = [e for e in events_to_delete if e not in locked_events]
                messages.warning(
                    request,
                    f"{lock_message} ロック中のイベント{len(locked_events)}件をスキップしました。",
                )
                if not events_to_delete:
                    return redirect('event:my_list')

        success_count = 0
        error_count = 0
        google_error_count = 0
        processed_event_ids = set()

        for event_to_delete in events_to_delete:
            if event_to_delete.pk in processed_event_ids:
                continue
            occurrences = get_cascade_occurrences(event_to_delete)
            lock_message = self._get_cascade_lock_message(request, occurrences)
            if lock_message:
                # 削除を中止した開催回は processed に入れない。
                # 入れると後続ループで未削除の兄弟がスキップされ、
                # 「親とロック中の子だけ残り以降が消える」不整合になる。
                messages.warning(
                    request,
                    f"{lock_message} 親イベントを含む削除を中止しました。",
                )
                continue

            google_event_ids = [
                occurrence.google_calendar_event_id
                for occurrence in occurrences
                if occurrence.google_calendar_event_id
            ]
            try:
                root_event_id = event_to_delete.pk
                deleted_count = delete_event_with_tombstones(
                    event_to_delete,
                    occurrences,
                )
                success_count += deleted_count
                # 実際に削除できた開催回だけを processed に記録する
                # （CASCADE で消えた子を後続ループが再処理しないため）。
                processed_event_ids.update(
                    occurrence.pk for occurrence in occurrences
                )
                logger.info(
                    "データベースからの削除成功: root_id=%s count=%s",
                    root_event_id,
                    deleted_count,
                )
            except Exception:
                logger.exception(
                    "イベントの削除に失敗: ID=%s",
                    event_to_delete.id,
                )
                error_count += 1
                continue

            google_error_count += self._delete_google_calendar_events(
                google_event_ids
            )

        if success_count > 0:
            if delete_subsequent:
                messages.success(request, f"{success_count}件のイベントを削除しました。")
            else:
                messages.success(request, "イベントを削除しました。")

        if error_count > 0:
            messages.error(request, f"{error_count}件のイベントの削除中にエラーが発生しました。")

        if google_error_count > 0:
            messages.warning(
                request,
                f"{google_error_count}件のGoogleカレンダー削除に失敗しました。"
                "後続の同期で再反映します。",
            )

        return redirect('event:my_list')

    @staticmethod
    def _get_cascade_lock_message(request, occurrences) -> str:
        """削除連鎖にVketロック中の開催回があればメッセージを返す。"""
        if request.user.is_superuser or request.user.is_staff:
            return ''

        from vket.services import get_vket_lock_info

        for occurrence in occurrences:
            locked, message = get_vket_lock_info(occurrence)
            if locked:
                return message
        return ''

    @staticmethod
    def _delete_google_calendar_events(event_ids) -> int:
        """Google Calendarの削除失敗数を返す。"""
        error_count = 0
        for event_id in event_ids:
            try:
                calendar_service = GoogleCalendarService(
                    calendar_id=GOOGLE_CALENDAR_ID,
                    credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
                )
                logger.info(
                    "Googleカレンダーからの削除を試行: Event ID=%s",
                    event_id,
                )
                calendar_service.delete_event(event_id)
            except Exception:
                logger.exception(
                    "Googleカレンダーからの削除失敗: Event ID=%s",
                    event_id,
                )
                error_count += 1
        return error_count


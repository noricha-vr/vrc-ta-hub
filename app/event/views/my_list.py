import logging
from datetime import date, time, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView

from community.services import activate_community
from event.models import Event, EventDetail
from event_calendar.calendar_utils import create_calendar_entry_url
from event_calendar.models import CalendarEntry
from utils.vrchat_time import get_vrchat_today

logger = logging.getLogger(__name__)


class EventMyList(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'event/my_list.html'
    context_object_name = 'events'
    paginate_by = 20
    # 常に表示する未来のイベント数。それより先は「もっと見る」で開く
    VISIBLE_FUTURE_EVENTS = 2

    def _get_user_communities(self):
        """ユーザーが管理者である集会のID一覧を取得する"""
        return list(
            self.request.user.community_memberships.values_list('community_id', flat=True)
        )

    def _apply_community_query_param(self):
        """?community=<id> が指定されていればアクティブな集会をそこへ固定する。

        不正値・権限外の ID は黙って無視し、既存のセッション/フォールバック挙動に委ねる
        （community:switch も権限外を画面遷移だけで済ませるため、URL 直叩きの失敗を
        エラー表示せずに揃える）。
        """
        raw_community_id = self.request.GET.get('community')
        if not raw_community_id:
            return

        # クロスサイト起点のトップレベルナビゲーションではセッションを書き換えない
        # （active_community_id は集会更新・イベント作成の対象決定に使われる共有状態のため、
        #   CSRF 保護のない GET で外部サイトから切り替えられると別タブのフォーム対象がすり替わる）
        if self.request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return

        # 受理条件の判定と session 更新は community.services が正本
        activate_community(self.request.session, self.request.user, raw_community_id)

    def get(self, request, *args, **kwargs):
        # get_queryset / get_context_data の双方が更新後のセッションを読むよう、
        # 一覧の組み立て前にアクティブな集会を確定させる。
        self._apply_community_query_param()
        return super().get(request, *args, **kwargs)

    def _get_active_community(self):
        """アクティブな集会を取得する"""
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id:
            membership = self.request.user.community_memberships.filter(
                community_id=active_community_id
            ).select_related('community').first()
            if membership:
                return membership.community

        # フォールバック: 最初の管理集会
        membership = self.request.user.community_memberships.select_related('community').first()
        if membership:
            return membership.community

        return None

    def _get_user_communities_list(self):
        """ユーザーが管理者である集会のオブジェクト一覧を取得する"""
        communities = []

        # メンバーシップベースの集会
        for membership in self.request.user.community_memberships.select_related('community'):
            communities.append(membership.community)

        return communities

    def _get_warnings(self, community):
        """アクティブな集会に対する警告リストを取得する"""
        warnings = []
        if not community:
            return warnings

        # ポスター未設定警告
        if not community.poster_image:
            warnings.append({
                'type': 'warning',
                'message': 'ポスター画像が設定されていません。ポスター画像を設定しないと、集会一覧やトップページにイベントが表示されません。',
                'link': reverse('community:update'),
                'link_text': '設定する'
            })

        # 今後のイベントなし警告
        future_events = Event.objects.filter(
            community=community,
            date__gte=timezone.now().date()
        ).exists()
        if not future_events:
            warnings.append({
                'type': 'info',
                'message': '今後のイベントが登録されていません。',
                'link': reverse('event:calendar_create'),
                'link_text': 'イベントを登録'
            })

        return warnings

    def get_queryset(self):
        today = get_vrchat_today()

        community_ids = self._get_target_community_ids()

        # 未来のイベントはページ送りに含めず、1ページ目だけに get_context_data で差し込む
        # （定期生成で数ヶ月先まで並ぶため、ページ送りに入れると過去の枠を食い、2ページ目にもこぼれる）
        return Event.objects.filter(
            community_id__in=community_ids,
            date__lt=today
        ).select_related('community').prefetch_related(
            'community__twitter_template'
        ).order_by('-date', '-start_time')

    def _get_target_community_ids(self):
        """一覧の対象にする集会ID（アクティブな集会があればそれだけ）"""
        user_community_ids = self._get_user_communities()
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id and active_community_id in user_community_ids:
            return [active_community_id]
        return user_community_ids

    def _get_future_events(self):
        """未来のイベントを取得し、直近の VISIBLE_FUTURE_EVENTS 件以外を畳む対象にする。

        畳んだイベントは「もっと見る」で開く。登録直後（?created=<id>）に
        登録したイベントが畳む側にあれば、最初から開いた状態にする。

        Returns:
            tuple[list, bool]: (未来のイベント, 最初から開くか)
        """
        future_events = list(
            Event.objects.filter(
                community_id__in=self._get_target_community_ids(),
                date__gte=get_vrchat_today()
            ).select_related('community').prefetch_related(
                'community__twitter_template'
            ).order_by('date', 'start_time')
        )
        visible = self.VISIBLE_FUTURE_EVENTS
        for index, event in enumerate(future_events):
            event.is_collapsed = index >= visible
            event.show_more_button_after = (
                index == visible - 1 and len(future_events) > visible
            )

        try:
            created_id = int(self.request.GET.get('created', ''))
        except ValueError:
            created_id = None
        open_more = any(
            event.is_collapsed and event.id == created_id
            for event in future_events
        )
        return future_events, open_more

    def set_vrc_event_calendar_post_url(self, queryset: QuerySet) -> QuerySet:
        """イベントのGoogleフォームのURLを設定する"""
        today = get_vrchat_today()
        # CalendarEntry は集会単位なので、集会ごとに1回だけ取得して使い回す
        calendar_entries = {}
        for event in queryset:
            if today > event.date:
                continue
            if event.community_id not in calendar_entries:
                calendar_entries[event.community_id] = CalendarEntry.get_or_create_from_event(event)
            event.calendar_url = create_calendar_entry_url(
                event, calendar_entry=calendar_entries[event.community_id]
            )
        return queryset

    def _set_twitter_button_flags(self, events):
        """イベントごとにTwitterボタン表示フラグを設定する

        Args:
            events (list): イベントリスト

        Returns:
            list: Twitterボタン表示フラグが設定されたイベントリスト
        """
        today = get_vrchat_today()
        for event in events:
            # イベント日から1週間後の日付を計算
            twitter_display_until = event.date + timedelta(days=7)
            # イベント日から1週間以内ならTwitterボタンを表示
            event.twitter_button_active = today <= twitter_display_until
        return events

    def _attach_edit_flags(self, events):
        """各イベントに開始時刻編集用のフラグを付与する。

        - can_edit_event: 集会の管理者（owner/staff）または superuser
        - vket_locked: Vket コラボ期間中で編集不可（superuser/is_staff は False）
        """
        from vket.models import VketParticipation
        from vket.services import get_vket_lock_info

        user = self.request.user
        today = get_vrchat_today()
        # 有効な Vket 参加がない集会のイベントはロックされないので、1件ずつの判定を省く
        vket_community_ids = set(
            VketParticipation.objects.filter(
                community_id__in={event.community_id for event in events},
                lifecycle=VketParticipation.Lifecycle.ACTIVE,
            ).values_list('community_id', flat=True)
        )
        # community 単位で権限判定を1回にまとめる（N+1回避）
        community_edit_cache = {}
        for event in events:
            community_id = event.community_id
            if community_id not in community_edit_cache:
                community_edit_cache[community_id] = (
                    user.is_superuser or event.community.can_edit(user)
                )
            event.can_edit_event = community_edit_cache[community_id] and event.date >= today

            # vket_locked はテンプレートで can_edit_event が True の時だけ参照する
            if (
                user.is_superuser or user.is_staff
                or not event.can_edit_event
                or community_id not in vket_community_ids
            ):
                event.vket_locked = False
            else:
                locked, _ = get_vket_lock_info(event)
                event.vket_locked = locked
        return events

    def _attach_event_details(self, events):
        """イベントごとにイベント詳細情報を取得・設定する

        Args:
            events (list): イベントリスト

        Returns:
            list: イベント詳細が添付されたイベントリスト
        """
        # イベントIDのリストを取得
        event_ids = [event.id for event in events]

        if event_ids:
            # イベント詳細を一括取得（管理画面のため全ステータスを含む）
            event_details = EventDetail.objects.filter(
                event_id__in=event_ids
            ).select_related('event').order_by('created_at')

            # イベント詳細をイベントIDごとに整理
            event_detail_dict = {}
            for detail in event_details:
                if detail.event_id not in event_detail_dict:
                    event_detail_dict[detail.event_id] = []
                event_detail_dict[detail.event_id].append(detail)

            # 各イベントに詳細リストを設定
            for event in events:
                event.detail_list = event_detail_dict.get(event.id, [])
        else:
            # イベントが存在しない場合は空のリストを設定
            for event in events:
                event.detail_list = []

        return events

    def _prepare_pagination_params(self):
        """ページネーション用のGETパラメータを準備する

        Returns:
            str: エンコードされたクエリパラメータ
        """
        query_params = self.request.GET.copy()
        for key in ('page', 'created'):
            if key in query_params:
                del query_params[key]
        return query_params.urlencode()

    def get_context_data(self, **kwargs):
        """テンプレートに渡すコンテキストデータを準備する

        各機能は専用のプライベートメソッドに分割され、
        このメソッドではそれらを順番に呼び出して結果を組み合わせる
        """
        context = super().get_context_data(**kwargs)

        # コミュニティ情報を取得（アクティブな集会）
        active_community = self._get_active_community()
        context['community'] = active_community
        context['active_community'] = active_community

        # 所属集会一覧を取得
        context['communities'] = self._get_user_communities_list()

        # 警告リストを取得
        context['warnings'] = self._get_warnings(active_community)

        # イベントリストを取得（1ページ目だけ未来のイベントを先頭に差し込む）
        events = list(context['events'])
        context['open_more_future_events'] = False
        if context['page_obj'].number == 1:
            future_events, open_more = self._get_future_events()
            events = future_events + events
            context['open_more_future_events'] = open_more

        # イベントにカレンダーURLを設定
        events = self.set_vrc_event_calendar_post_url(events)

        # Twitterボタン表示用のフラグを設定
        events = self._set_twitter_button_flags(events)

        # イベント詳細情報を取得・設定
        events = self._attach_event_details(events)

        # 開始時刻編集ボタン表示用のフラグを設定
        events = self._attach_edit_flags(events)

        # 更新されたイベントリストをコンテキストに再設定
        context['events'] = events
        # ページネーション用のパラメータを設定
        context['current_query_params'] = self._prepare_pagination_params()

        # Vketコラボバナー情報
        context['vket_banner'] = self._get_vket_banner(active_community)

        # 未来のイベントが存在するかをチェック
        today = get_vrchat_today()
        future_events_exist = any(event.date >= today for event in events)
        context['has_future_events'] = future_events_exist

        return context

    def _get_vket_schedule_milestones(self, collaboration):
        """コラボ設定の説明会・お疲れ様会を、不正な値を除いて取得する。"""
        settings = collaboration.settings_json
        if not isinstance(settings, dict):
            return {}
        schedule = settings.get('schedule_milestones')
        if not isinstance(schedule, dict):
            return {}

        milestones = {}
        for key, default_label in (
            ('kickoff', '説明会・キックオフ'),
            ('after_party', 'お疲れ様でした会'),
        ):
            value = schedule.get(key)
            if not isinstance(value, dict):
                continue
            raw_date = value.get('date')
            if not isinstance(raw_date, str):
                continue
            try:
                milestone_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            if milestone_date.isoformat() != raw_date:
                continue

            milestone_time = None
            raw_time = value.get('time')
            if isinstance(raw_time, str):
                try:
                    parsed_time = time.fromisoformat(raw_time)
                    if parsed_time.strftime('%H:%M') == raw_time:
                        milestone_time = parsed_time
                except ValueError:
                    pass
            label = value.get('label')
            milestones[key] = {
                'key': key,
                'date': milestone_date,
                'label': label.strip() if isinstance(label, str) and label.strip() else default_label,
                'tentative': value.get('tentative') is True,
                'time': milestone_time,
            }
        return milestones

    def _get_vket_banner(self, community):
        """Vketコラボバナーに必要な情報を返す。

        DRAFT/ARCHIVEDと終了済みを除外した最新のコラボを取得し、
        アクティブな集会の申込状況に応じて、次の節目を1つ表示する。

        Args:
            community: アクティブな集会（Noneの場合あり）

        Returns:
            dict or None: バナー表示に必要な情報。非表示の場合はNone
        """
        from vket.models import VketCollaboration, VketParticipation
        from vket.views.helpers import _is_vket_admin

        today = timezone.localdate()
        collaborations = (
            VketCollaboration.objects
            .exclude(phase__in=[
                VketCollaboration.Phase.DRAFT,
                VketCollaboration.Phase.ARCHIVED,
            ])
            .only(
                'name', 'phase', 'period_start', 'period_end',
                'registration_deadline', 'lt_deadline', 'settings_json',
            )
            .order_by('-period_start', '-id')
        )
        collaboration = None
        for candidate in collaborations.iterator():
            schedule = self._get_vket_schedule_milestones(candidate)
            after_party = schedule.get('after_party')
            display_until = max(
                candidate.period_end,
                after_party['date'] if after_party else candidate.period_end,
            )
            if today <= display_until:
                collaboration = candidate
                break
        if collaboration is None:
            return None

        is_vket_admin = _is_vket_admin(self.request.user)

        participation = None
        if community:
            participation = VketParticipation.objects.filter(
                collaboration=collaboration,
                community=community,
            ).first()
        has_participation = participation is not None
        has_applied = (
            has_participation
            and participation.progress != VketParticipation.Progress.NOT_APPLIED
        )
        is_inactive_participation = (
            has_participation
            and participation.lifecycle != VketParticipation.Lifecycle.ACTIVE
        )
        registration_closed = (
            not has_applied
            and not is_inactive_participation
            and today > collaboration.registration_deadline
        )

        milestones = []
        if (
            (not has_applied and not is_inactive_participation)
            or (is_vket_admin and community is None)
        ):
            milestones.append({
                'key': 'registration_deadline',
                'date': collaboration.registration_deadline,
                'label': '参加表明の締切',
            })
        if (
            (has_applied and not is_inactive_participation)
            or (is_vket_admin and community is None)
        ):
            milestones.append({
                'key': 'lt_deadline',
                'date': collaboration.lt_deadline,
                'label': '発表者・テーマの登録締切',
            })
            if 'kickoff' in schedule:
                milestones.append(schedule['kickoff'])
            if participation and participation.effective_date:
                milestones.append({
                    'key': 'community_event',
                    'date': participation.effective_date,
                    'time': participation.effective_start_time,
                    'label': (
                        'あなたの集会の開催日'
                        if participation.confirmed_date
                        else 'あなたの集会の開催日（希望）'
                    ),
                })
            elif is_vket_admin and community is None:
                milestones.append({
                    'key': 'period_start',
                    'date': collaboration.period_start,
                    'label': '会期の開始',
                })
            if 'after_party' in schedule:
                milestones.append(schedule['after_party'])
        milestone = min(
            (item for item in milestones if item['date'] >= today),
            key=lambda item: item['date'],
            default=None,
        )

        is_during_event = (
            collaboration.period_start <= today <= collaboration.period_end
        )

        phase = collaboration.phase
        period = (
            f'{collaboration.period_start.month}/{collaboration.period_start.day}'
            f'\u301c{collaboration.period_end.month}/{collaboration.period_end.day}'
        )
        days_until = None
        date_display = ''
        time_display = ''
        tentative = False
        if milestone:
            days_until = (milestone['date'] - today).days
            tentative = milestone.get('tentative', False)
            weekday = '月火水木金土日'[milestone['date'].weekday()]
            date_display = f"{milestone['date'].month}/{milestone['date'].day}（{weekday}）"
            if tentative:
                date_display += '頃'
            start_time = milestone.get('time')
            if start_time:
                if tentative:
                    minutes = f'{start_time.minute}分' if start_time.minute else ''
                    time_display = f'{start_time.hour}時{minutes}頃'
                else:
                    time_display = start_time.strftime('%H:%M')
            message = milestone['label']
            if days_until == 0:
                message = f'{time_display} から {message}です' if time_display else f'{message}です'
        elif is_inactive_participation:
            message = ''
        elif registration_closed:
            message = '参加申し込みは締め切りました'
        elif not has_applied and phase == VketCollaboration.Phase.ENTRY_OPEN:
            message = '参加申し込み受付中'
        elif is_during_event:
            message = '開催中'
        else:
            message = '次のご案内をお待ちください'

        if (
            not has_participation
            and phase == VketCollaboration.Phase.ENTRY_OPEN
            and not registration_closed
        ):
            url_name = 'vket:apply'
            button_text = '参加申し込み'
            button_icon = 'fas fa-pen-to-square'
        else:
            url_name = 'vket:status'
            button_text = '参加状況を確認'
            button_icon = 'fas fa-pen-to-square'

        logger.info('vket.banner_displayed', extra={
            'collaboration_id': collaboration.pk,
            'milestone': milestone['key'] if milestone else 'none',
            'days_until': days_until,
            'tentative': tentative,
        })
        return {
            'collaboration': collaboration,
            'message': message,
            'subtitle': f'{collaboration.name} {period}',
            'days_until': days_until,
            'milestone': milestone,
            'date_display': date_display,
            'time_display': time_display,
            'tentative': tentative,
            'url_name': url_name,
            'url_pk': collaboration.pk,
            'button_text': button_text,
            'button_icon': button_icon,
            'has_participation': has_participation,
            # 申込・参加状況のページは集会の選択が前提なので、所属が無ければ出さない
            'show_participation_link': community is not None and not registration_closed,
            'is_vket_admin': is_vket_admin,
        }

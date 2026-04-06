import logging
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView

from event.models import Event, EventDetail
from event_calendar.calendar_utils import create_calendar_entry_url
from utils.vrchat_time import get_vrchat_today

logger = logging.getLogger(__name__)


class EventMyList(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'event/my_list.html'
    context_object_name = 'events'
    paginate_by = 20

    def _get_user_communities(self):
        """ユーザーが管理者である集会のID一覧を取得する"""
        return list(
            self.request.user.community_memberships.values_list('community_id', flat=True)
        )

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

        user_community_ids = self._get_user_communities()

        # アクティブな集会が設定されている場合はその集会のみを対象に
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id and active_community_id in user_community_ids:
            community_ids = [active_community_id]
        else:
            # フォールバック: 全ての管理集会
            community_ids = user_community_ids

        # 未来のイベントを最大2つまで取得
        future_events = Event.objects.filter(
            community_id__in=community_ids,
            date__gte=today
        ).select_related('community').order_by('date', 'start_time')[:2]

        # 過去のイベントを取得
        past_events = Event.objects.filter(
            community_id__in=community_ids,
            date__lt=today
        ).select_related('community').order_by('-date', '-start_time')

        # 未来のイベントと過去のイベントを結合
        return list(future_events) + list(past_events)

    def set_vrc_event_calendar_post_url(self, queryset: QuerySet) -> QuerySet:
        """イベントのGoogleフォームのURLを設定する"""
        for event in queryset:
            if get_vrchat_today() > event.date:
                continue
            event.calendar_url = create_calendar_entry_url(event)
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
        if 'page' in query_params:
            del query_params['page']
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

        # イベントリストを取得
        events = context['events']

        # イベントにカレンダーURLを設定
        events = self.set_vrc_event_calendar_post_url(events)

        # Twitterボタン表示用のフラグを設定
        events = self._set_twitter_button_flags(events)

        # イベント詳細情報を取得・設定
        events = self._attach_event_details(events)

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

    def _get_vket_banner(self, community):
        """Vketコラボバナーに必要な情報を返す。

        DRAFT/ARCHIVEDを除外した最新のコラボを取得し、
        フェーズ・日付に基づいて状態メッセージとリンク先を決定する。

        Args:
            community: アクティブな集会（Noneの場合あり）

        Returns:
            dict or None: バナー表示に必要な情報。非表示の場合はNone
        """
        from vket.models import VketCollaboration, VketParticipation

        collaboration = (
            VketCollaboration.objects
            .exclude(phase__in=[
                VketCollaboration.Phase.DRAFT,
                VketCollaboration.Phase.ARCHIVED,
            ])
            .order_by('-period_start', '-id')
            .first()
        )
        if not collaboration:
            return None

        today = timezone.localdate()

        has_participation = False
        if community:
            has_participation = VketParticipation.objects.filter(
                collaboration=collaboration,
                community=community,
            ).exists()

        is_during_event = (
            collaboration.period_start <= today <= collaboration.period_end
        )

        phase = collaboration.phase
        period = (
            f'{collaboration.period_start.month}/{collaboration.period_start.day}'
            f'\u301c{collaboration.period_end.month}/{collaboration.period_end.day}'
        )
        if is_during_event:
            message = f'{collaboration.name} 開催中！（{collaboration.period_end.month}/{collaboration.period_end.day}まで）'
        elif phase == VketCollaboration.Phase.ENTRY_OPEN:
            message = f'{collaboration.name}（{period}）参加申し込み受付中'
        elif phase in (
            VketCollaboration.Phase.SCHEDULING,
            VketCollaboration.Phase.LT_COLLECTION,
        ):
            message = f'{collaboration.name}（{period}）'
        elif phase in (
            VketCollaboration.Phase.ANNOUNCEMENT,
            VketCollaboration.Phase.LOCKED,
        ):
            message = f'{collaboration.name}（{period}）'
        else:
            return None

        if (
            not has_participation
            and phase == VketCollaboration.Phase.ENTRY_OPEN
        ):
            url_name = 'vket:apply'
            button_text = '参加申し込み'
        else:
            url_name = 'vket:status'
            button_text = '参加状況を確認'

        return {
            'collaboration': collaboration,
            'message': message,
            'url_name': url_name,
            'url_pk': collaboration.pk,
            'button_text': button_text,
            'has_participation': has_participation,
        }

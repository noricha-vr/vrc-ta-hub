# Create your views here.
# from corsheaders.middleware import CorsMiddleware  # No longer needed
import re

from allauth.socialaccount.models import SocialAccount
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import OperationalError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers, viewsets, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from community.models import BotInstallation, Community, CommunityMember
from event.datetime_lock import (
    EVENT_DETAIL_DATETIME_LOCK_MESSAGE,
    has_event_detail_duration_changed,
    has_event_detail_start_time_changed,
    is_event_datetime_locked,
)
from event.models import Event, EventDetail, RecurrenceRule
from event.services.application_review import ApplicationAlreadyReviewed, review_application
from .authentication import APIKeyAuthentication
from .base import DatabaseReconnectListMixin
from .serializers import (
    CommunitySerializer, EventSerializer, EventDetailSerializer, EventDetailWriteSerializer,
    RecurrenceRuleSerializer, RecurrenceRuleDeleteSerializer, GatheringListSerializer,
    GatheringListSchemaSerializer, CommunityByDiscordSerializer, BotInstallationSerializer,
)


# CORSMixin is no longer needed as CORS is handled by Django middleware


class IsSuperuser(BasePermission):
    message = '管理者のみ利用できます。'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class CommunityByDiscordAPIView(APIView):
    """Discord 連携済みユーザーの主催・スタッフ所属を管理者向けに返す。"""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]

    @extend_schema(
        summary='Discord ユーザーの所属集会取得',
        description=(
            'API キー（X-API-Key）またはセッションで認証したスーパーユーザー限定。'
            'Discord 連携済みユーザーが主催者またはスタッフの集会を ID 順に返します。'
            '未連携または所属なしの場合は空の一覧を返します。'
        ),
        tags=['Community'],
        parameters=[
            OpenApiParameter(
                name='discord_id',
                description='17〜20 桁の数字で表した Discord ユーザー ID',
                required=True,
                location=OpenApiParameter.QUERY,
                type={'type': 'string', 'pattern': '^[0-9]{17,20}$', 'minLength': 17, 'maxLength': 20},
            ),
        ],
        responses={200: CommunityByDiscordSerializer},
    )
    def get(self, request):
        discord_id = request.query_params.get('discord_id', '')
        if re.fullmatch(r'[0-9]{17,20}', discord_id) is None:
            raise ValidationError({'detail': 'discord_id は17〜20桁の数字で指定してください。'})

        linked_users = SocialAccount.objects.filter(provider='discord', uid=discord_id).values('user_id')
        memberships = CommunityMember.objects.filter(
            user_id__in=linked_users,
            role__in=[CommunityMember.Role.OWNER, CommunityMember.Role.STAFF],
        ).select_related('community').order_by('community_id')
        serializer = CommunityByDiscordSerializer({'discord_id': discord_id, 'communities': memberships})
        return Response(serializer.data)


def _validate_bot_guild_id(guild_id):
    if not isinstance(guild_id, str) or re.fullmatch(r'[0-9]{17,20}', guild_id) is None:
        raise ValidationError({'guild_id': 'guild_id は17〜20桁の数字の文字列で指定してください。'})
    return guild_id


class BotInstallationAPIView(APIView):
    """Discord サーバーの有効な Bot 導入記録を返す。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]

    @extend_schema(
        summary='Bot の導入記録取得',
        description=(
            'スーパーユーザーの API キー（X-API-Key）限定。'
            '指定サーバーの外れていない導入記録（installed / configured）を返します。'
            '通知先チャンネル ID と Webhook の設定有無、Hub の送信経路（webhook / none）も返します。Webhook URL は返しません。'
            '該当記録がない場合は 404、不正な guild_id は 400。'
            'エラーは共通の detail / code 形式です。'
        ),
        tags=['Community'],
        parameters=[OpenApiParameter(
            name='guild_id', required=True, location=OpenApiParameter.QUERY,
            description='17〜20 桁の数字で表した Discord サーバー ID',
            type={'type': 'string', 'pattern': '^[0-9]{17,20}$', 'minLength': 17, 'maxLength': 20},
        )],
        responses={200: BotInstallationSerializer, 400: OpenApiTypes.OBJECT,
                   403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        guild_id = _validate_bot_guild_id(request.query_params.get('guild_id'))
        # 外れていない記録は active_guild_id の UNIQUE で一意に引ける。
        installation = get_object_or_404(BotInstallation.objects.select_related('community'), active_guild_id=guild_id)
        return Response(BotInstallationSerializer(installation).data)


class BotInstallationStatusAPIView(APIView):
    """Bot からの設定完了・取り外し通知を反映する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='Bot の導入状態更新',
        description=(
            'スーパーユーザーの書き込み API キー（X-API-Key）限定。'
            'JSON の installation_id（取得 API の id）・guild_id・status（configured / removed）を受け取り、状態と時刻を記録します。'
            '同じサーバーに後から別の導入記録ができていても、古い記録宛ての通知は新しい記録を変えず 404 になります。'
            'configured の再送は時刻を変えず 200 を返します。installed への変更は 400。'
            'configured では任意の notification_channel_id（17〜20 桁の数字の文字列）を保存し、省略時は保持します。'
            'removed では通知先チャンネル ID を消します。'
            '外れた記録は更新せず、有効な記録がない場合は 404。'
            'エラーは共通の detail / code 形式です。'
        ),
        tags=['Community'],
        request=inline_serializer(name='BotInstallationStatus', fields={
            'installation_id': serializers.IntegerField(min_value=1),
            'guild_id': serializers.RegexField(r'^[0-9]{17,20}$', trim_whitespace=False),
            'status': serializers.ChoiceField(choices=['configured', 'removed']),
            'notification_channel_id': serializers.RegexField(
                r'^[0-9]{17,20}$', min_length=17, max_length=20, required=False, trim_whitespace=False,
            ),
        }),
        responses={200: BotInstallationSerializer, 400: OpenApiTypes.OBJECT,
                   403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        if not isinstance(request.data, dict):
            raise ValidationError({'detail': 'JSON オブジェクトを指定してください。'})
        guild_id = _validate_bot_guild_id(request.data.get('guild_id'))
        installation_id = request.data.get('installation_id')
        if type(installation_id) is not int or installation_id < 1:
            raise ValidationError({'installation_id': 'installation_id は正の整数で指定してください。'})
        new_status = request.data.get('status')
        if new_status not in (BotInstallation.Status.CONFIGURED, BotInstallation.Status.REMOVED):
            raise ValidationError({'status': 'configured または removed を指定してください。'})
        if 'notification_channel_id' in request.data:
            channel_id = request.data['notification_channel_id']
            if not isinstance(channel_id, str) or re.fullmatch(r'[0-9]{17,20}', channel_id) is None:
                raise ValidationError({
                    'notification_channel_id': 'notification_channel_id は17〜20桁の数字の文字列で指定してください。',
                })

        with transaction.atomic():
            # 主キーでロックし、再導入後に遅れて届いた古い通知が新しい記録を変えないよう guild_id も照合する。
            installation = get_object_or_404(
                BotInstallation.objects.select_for_update().exclude(status=BotInstallation.Status.REMOVED),
                pk=installation_id, guild_id=guild_id,
            )
            update_fields = []
            if installation.status != new_status:
                timestamp_field = 'configured_at' if new_status == BotInstallation.Status.CONFIGURED else 'removed_at'
                installation.status = new_status
                setattr(installation, timestamp_field, timezone.now())
                update_fields.extend(['status', timestamp_field])
            if new_status == BotInstallation.Status.REMOVED:
                installation.notification_channel_id = None
                update_fields.append('notification_channel_id')
            elif 'notification_channel_id' in request.data:
                installation.notification_channel_id = request.data['notification_channel_id']
                update_fields.append('notification_channel_id')
            if update_fields:
                installation.save(update_fields=update_fields)
        return Response(BotInstallationSerializer(installation).data)


class EventDetailReviewAPIView(APIView):
    """Discord で主催・スタッフが押した審査結果を反映する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='発表申請の承認・却下',
        description='スーパーユーザーの書き込み API キー限定。Discord 連携先が対象集会の主催・スタッフであることを確認します。',
        tags=['EventDetail API'],
        request=inline_serializer(name='EventDetailReviewRequest', fields={
            'decision': serializers.ChoiceField(choices=['approved', 'rejected']),
            'discord_id': serializers.RegexField(r'^[0-9]{17,20}$'),
        }),
        responses={200: inline_serializer(name='EventDetailReviewResponse', fields={
            'id': serializers.IntegerField(),
            'status': serializers.ChoiceField(choices=['approved', 'rejected']),
            'warnings': serializers.ListField(child=serializers.CharField(), required=False),
        })},
    )
    def post(self, request, pk):
        if not isinstance(request.data, dict):
            raise ValidationError({'detail': 'JSON オブジェクトを指定してください。'})
        decision = request.data.get('decision')
        if decision not in ('approved', 'rejected'):
            raise ValidationError({'decision': 'approved または rejected を指定してください。'})
        discord_id = request.data.get('discord_id')
        if not isinstance(discord_id, str) or re.fullmatch(r'[0-9]{17,20}', discord_id) is None:
            raise ValidationError({'discord_id': 'discord_id は17〜20桁の数字の文字列で指定してください。'})

        event_detail = get_object_or_404(EventDetail.objects.select_related('event__community'), pk=pk)
        account = SocialAccount.objects.filter(provider='discord', uid=discord_id).select_related('user').first()
        if account is None:
            raise PermissionDenied('この申請を確認する権限がありません。')
        try:
            result = self._review_retrying_deadlock(event_detail, decision, account.user, request)
        except _ReviewDeadlock:
            # 親イベントの削除などとロック順が交差した。押し直し（再送）で決着するので 503 にする。
            return Response({
                'detail': '混み合っています。しばらくしてから再度お試しください。',
                'code': 'retry',
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={'Retry-After': '5'})
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ApplicationAlreadyReviewed:
            return Response({
                'detail': 'この申請は既に処理されています。',
                'code': 'already_reviewed',
            }, status=status.HTTP_409_CONFLICT)
        return Response({
            'id': result.event_detail.pk,
            'status': result.event_detail.status,
            **({'warnings': result.warnings} if result.warnings else {}),
        })


    @staticmethod
    def _review_retrying_deadlock(event_detail, decision, reviewer, request):
        """MySQL のデッドロック・ロック待ち超過は 1 回だけ読み直して再試行する。"""
        for attempt in range(2):
            try:
                return review_application(
                    event_detail, decision, reviewer=reviewer, request=request,
                    api_key_id=request.auth.pk,
                )
            except OperationalError as error:
                if not error.args or error.args[0] not in _RETRYABLE_LOCK_ERRORS:
                    raise
                if attempt == 1:
                    raise _ReviewDeadlock from error
                # 削除で負けた側なら、読み直しで 404 になる。
                event_detail = get_object_or_404(
                    EventDetail.objects.select_related('event__community'), pk=event_detail.pk,
                )


_RETRYABLE_LOCK_ERRORS = (1213, 1205)


class _ReviewDeadlock(Exception):
    """再試行してもロックが取れなかった。"""


class CommunityFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr='icontains')
    weekdays = filters.CharFilter(method='filter_weekdays')

    class Meta:
        model = Community
        fields = ['name', 'weekdays']

    def filter_weekdays(self, queryset, name, value):
        return queryset.filter(weekdays__contains=value)


@extend_schema_view(
    list=extend_schema(
        summary="集会一覧取得",
        description="承認済みでアクティブな集会の一覧を取得します。",
        tags=["Community"]
    ),
    retrieve=extend_schema(
        summary="集会詳細取得",
        description="指定IDの集会詳細情報を取得します。",
        tags=["Community"]
    )
)
class CommunityViewSet(DatabaseReconnectListMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Community.objects.filter(
        end_at__isnull=True,
        status='approved',
    ).exclude(
        tags=[]
    ).order_by('-pk')
    serializer_class = CommunitySerializer
    filterset_class = CommunityFilter
    filter_backends = [DjangoFilterBackend]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]

    @extend_schema(
        summary="TaAGatheringListSys向け集会一覧取得",
        description=(
            "VRChat ワールド内の TaAGatheringListSys がそのまま読み込める JSON 形式で、"
            "承認済みかつアクティブな技術系・学術系集会の一覧を返します。"
        ),
        tags=["Community"],
        # OpenAPI は fields を持つ専用 serializer を参照し、to_representation 実装との差分で壊れないようにする。
        responses=GatheringListSchemaSerializer(many=True),
    )
    @action(detail=False, methods=['get'], url_path='gathering-list')
    def gathering_list(self, request):
        communities = Community.objects.filter(
            end_at__isnull=True,
            status='approved',
        )
        communities = [
            community
            for community in communities
            if {'tech', 'academic'} & set(GatheringListSerializer.normalize_choice_list(community.tags))
        ]

        ordered_communities = sorted(
            communities,
            key=lambda community: (
                GatheringListSerializer.get_weekday_sort_index(community.weekdays),
                community.name,
            ),
        )

        serializer = GatheringListSerializer(
            ordered_communities,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)


class EventFilter(filters.FilterSet):
    community = filters.NumberFilter(field_name='community_id')
    name = filters.CharFilter(
        field_name='community__name', lookup_expr='icontains')
    weekday = filters.CharFilter(field_name='weekday')
    start_date = filters.DateFilter(field_name='date', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = Event
        fields = ['community', 'name', 'weekday', 'start_date', 'end_date']


@extend_schema_view(
    list=extend_schema(
        summary="イベント一覧取得",
        description="今後開催予定のイベント一覧を取得します。",
        tags=["Event"]
    ),
    retrieve=extend_schema(
        summary="イベント詳細取得",
        description="指定IDのイベント詳細情報を取得します。",
        tags=["Event"]
    )
)
class EventViewSet(DatabaseReconnectListMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.filter(
        date__gte=timezone.now().date(),
        community__status='approved',
        community__end_at__isnull=True,
    ).select_related('community').order_by('date', 'start_time')
    serializer_class = EventSerializer
    filterset_class = EventFilter
    filter_backends = [DjangoFilterBackend]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]


class EventDetailFilter(filters.FilterSet):
    community = filters.NumberFilter(field_name='event__community_id')
    theme = filters.CharFilter(lookup_expr='icontains')
    speaker = filters.CharFilter(lookup_expr='icontains')
    start_date = filters.DateFilter(field_name='event__date', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='event__date', lookup_expr='lte')
    start_time = filters.TimeFilter(field_name='start_time')

    class Meta:
        model = EventDetail
        fields = ['community', 'theme', 'speaker', 'start_date', 'end_date', 'start_time']


@extend_schema_view(
    list=extend_schema(
        summary="イベント詳細一覧取得（公開）",
        description="承認済み集会のイベント詳細一覧を取得します。",
        tags=["EventDetail"]
    ),
    retrieve=extend_schema(
        summary="イベント詳細取得（公開）",
        description="指定IDのイベント詳細情報を取得します。",
        tags=["EventDetail"]
    )
)
class EventDetailViewSet(DatabaseReconnectListMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EventDetail.objects.filter(
        event__community__status='approved',
        event__community__end_at__isnull=True,
        status='approved'
    ).select_related('event', 'event__community').order_by('event__date', 'start_time')
    serializer_class = EventDetailSerializer
    filterset_class = EventDetailFilter
    filter_backends = [DjangoFilterBackend]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]


@extend_schema_view(
    list=extend_schema(
        summary="イベント詳細一覧取得（認証必須）",
        description="認証ユーザーのイベント詳細一覧を取得します。コミュニティオーナーは自分のイベントのみ、Superuserは全イベントを取得できます。",
        tags=["EventDetail API"],
        parameters=[
            OpenApiParameter(
                name="Authorization",
                description="Bearer {APIキー} 形式で指定",
                required=True,
                type=str,
                location=OpenApiParameter.HEADER,
            ),
        ],
    ),
    create=extend_schema(
        summary="イベント詳細作成",
        description="新しいイベント詳細を作成します。generate_from_pdfをtrueに設定し、slide_fileにPDFをアップロードすると内容が自動生成されます。",
        tags=["EventDetail API"],
        examples=[
            OpenApiExample(
                "LT作成例",
                value={
                    "event": 1,
                    "detail_type": "LT",
                    "start_time": "20:00:00",
                    "duration": 30,
                    "speaker": "発表者名",
                    "theme": "発表テーマ",
                    "h1": "タイトル",
                    "contents": "内容",
                    "generate_from_pdf": False
                },
            ),
        ],
    ),
    retrieve=extend_schema(
        summary="イベント詳細取得",
        description="指定IDのイベント詳細を取得します。",
        tags=["EventDetail API"]
    ),
    update=extend_schema(
        summary="イベント詳細更新",
        description="イベント詳細を更新します。",
        tags=["EventDetail API"]
    ),
    partial_update=extend_schema(
        summary="イベント詳細部分更新",
        description="イベント詳細を部分的に更新します。",
        tags=["EventDetail API"]
    ),
    destroy=extend_schema(
        summary="イベント詳細削除",
        description="イベント詳細を削除します。",
        tags=["EventDetail API"]
    ),
    my_events=extend_schema(
        summary="自分のイベント詳細一覧",
        description="認証ユーザーのコミュニティに紐づくイベント詳細一覧を取得します。",
        tags=["EventDetail API"]
    )
)
class EventDetailAPIViewSet(DatabaseReconnectListMixin, viewsets.ModelViewSet):
    """
    EventDetailのCRUD API
    認証: APIキーまたはセッション認証
    権限: コミュニティオーナーまたはSuperuser
    """
    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [UserRateThrottle]
    
    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return EventDetailSerializer
        return EventDetailWriteSerializer
    
    def get_queryset(self):
        user = self.request.user

        # Superuserは全て表示
        if user.is_superuser:
            return EventDetail.objects.all().select_related('event', 'event__community')

        # 一般ユーザーは自分のコミュニティのみ
        user_community_ids = user.community_memberships.values_list('community_id', flat=True)
        return EventDetail.objects.filter(
            event__community_id__in=user_community_ids
        ).select_related('event', 'event__community')

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        instance = serializer.instance
        target_event = serializer.validated_data.get('event', instance.event)
        errors = {}
        # ロック中イベントからの持ち出しを禁止する。移動を許すと「未ロックの兄弟
        # イベントへ移してから削除」でロックを迂回できてしまう。
        if target_event != instance.event and is_event_datetime_locked(instance.event, self.request.user):
            errors['event'] = [EVENT_DETAIL_DATETIME_LOCK_MESSAGE]
        if is_event_datetime_locked(target_event, self.request.user):
            if has_event_detail_start_time_changed(instance, serializer.validated_data.get('start_time')):
                errors['start_time'] = [EVENT_DETAIL_DATETIME_LOCK_MESSAGE]
            if has_event_detail_duration_changed(instance, serializer.validated_data.get('duration')):
                errors['duration'] = [EVENT_DETAIL_DATETIME_LOCK_MESSAGE]
        if errors:
            raise ValidationError(errors)
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # Vketコラボ期間中は運営調整済みの登壇情報を主催者が誤って消さないよう、EventDetail削除をブロックする
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked, message = get_vket_lock_info(instance.event)
            if locked:
                return Response(
                    {"detail": message, "code": "vket_locked"},
                    status=status.HTTP_403_FORBIDDEN
                )

        # 権限チェック
        if not request.user.is_superuser:
            if not instance.event.community.is_manager(request.user):
                return Response(
                    {
                        "detail": "このイベント詳細を削除する権限がありません。",
                        "code": "permission_denied",
                    },
                    status=status.HTTP_403_FORBIDDEN
                )

        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=False, methods=['get'])
    def my_events(self, request):
        """自分のコミュニティのイベント詳細一覧"""
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        summary="定期ルール一覧取得",
        description="定期イベントのルール一覧を取得します。",
        tags=["RecurrenceRule API"],
        parameters=[
            OpenApiParameter(
                name="Authorization",
                description="Bearer {APIキー} 形式で指定",
                required=True,
                type=str,
                location=OpenApiParameter.HEADER,
            ),
        ],
    ),
    retrieve=extend_schema(
        summary="定期ルール詳細取得",
        description="指定IDの定期ルール詳細を取得します。",
        tags=["RecurrenceRule API"]
    ),
    destroy=extend_schema(
        summary="定期ルール削除",
        description="定期ルールを削除します。関連する未来のイベントも削除されます。",
        tags=["RecurrenceRule API"]
    ),
    delete_future_events=extend_schema(
        summary="未来のイベントを削除",
        description="指定された定期ルールに関連する未来のイベントを削除します。",
        tags=["RecurrenceRule API"],
        request=RecurrenceRuleDeleteSerializer,
        responses={200: OpenApiTypes.OBJECT}
    )
)
class RecurrenceRuleViewSet(DatabaseReconnectListMixin, viewsets.ReadOnlyModelViewSet):
    """
    RecurrenceRuleのAPI
    認証: APIキーまたはセッション認証
    権限: Superuserのみ
    """
    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = RecurrenceRuleSerializer
    queryset = RecurrenceRule.objects.all()
    
    def get_queryset(self):
        user = self.request.user
        
        # Superuserのみアクセス可能
        if not user.is_superuser:
            return RecurrenceRule.objects.none()
        
        return RecurrenceRule.objects.all()
    
    @action(detail=True, methods=['post'])
    def delete_future_events(self, request, pk=None):
        """未来のイベントを削除"""
        recurrence_rule = self.get_object()
        serializer = RecurrenceRuleDeleteSerializer(data=request.data)
        
        if serializer.is_valid():
            delete_from_date = serializer.validated_data.get('delete_from_date')
            delete_rule = serializer.validated_data.get('delete_rule', True)
            
            # 未来のイベントを削除
            deleted_count = recurrence_rule.delete_future_events(delete_from_date)
            
            # ルール自体も削除する場合
            if delete_rule:
                recurrence_rule.delete(delete_future_events=False)  # 既に削除済みなのでFalse
                
                return Response({
                    'success': True,
                    'deleted_events_count': deleted_count,
                    'rule_deleted': True,
                    'message': f'{deleted_count}件のイベントを削除し、定期ルールも削除しました。'
                })
            
            return Response({
                'success': True,
                'deleted_events_count': deleted_count,
                'rule_deleted': False,
                'message': f'{deleted_count}件のイベントを削除しました。'
            })
        
        # 既存クライアント互換のためフィールド別エラーはそのまま残し、
        # 機械可読な detail/code を追加する。
        return Response(
            {
                **serializer.errors,
                'detail': 'リクエストの内容が不正です。',
                'code': 'validation_error',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def destroy(self, request, *args, **kwargs):
        """定期ルールを削除（未来のイベントも削除）"""
        instance = self.get_object()
        
        # 未来のイベント数を取得
        future_count = instance.delete_future_events()
        
        # ルールを削除
        instance.delete(delete_future_events=False)  # 既に削除済みなのでFalse
        
        return Response({
            'success': True,
            'deleted_events_count': future_count,
            'message': f'定期ルールと{future_count}件の未来のイベントを削除しました。'
        }, status=status.HTTP_200_OK)

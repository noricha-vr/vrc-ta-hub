# Create your views here.
# from corsheaders.middleware import CorsMiddleware  # No longer needed
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from community.models import Community
from event.models import Event, EventDetail, RecurrenceRule
from .authentication import APIKeyAuthentication
from .serializers import (
    CommunitySerializer, EventSerializer, EventDetailSerializer, EventDetailWriteSerializer,
    RecurrenceRuleSerializer, RecurrenceRuleDeleteSerializer
)


# CORSMixin is no longer needed as CORS is handled by Django middleware


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
@method_decorator(csrf_exempt, name='dispatch')
class CommunityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Community.objects.filter(
        end_at__isnull=True,
        status='approved'
    ).order_by('-pk')
    serializer_class = CommunitySerializer
    filterset_class = CommunityFilter
    filter_backends = [DjangoFilterBackend]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]


class EventFilter(filters.FilterSet):
    name = filters.CharFilter(
        field_name='community__name', lookup_expr='icontains')
    weekday = filters.CharFilter(field_name='weekday')
    start_date = filters.DateFilter(field_name='date', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = Event
        fields = ['name', 'weekday', 'start_date', 'end_date']


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
@method_decorator(csrf_exempt, name='dispatch')
class EventViewSet(viewsets.ReadOnlyModelViewSet):
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
    theme = filters.CharFilter(lookup_expr='icontains')
    speaker = filters.CharFilter(lookup_expr='icontains')
    start_date = filters.DateFilter(field_name='event__date', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='event__date', lookup_expr='lte')
    start_time = filters.TimeFilter(field_name='start_time')

    class Meta:
        model = EventDetail
        fields = ['theme', 'speaker', 'start_date', 'end_date', 'start_time']


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
@method_decorator(csrf_exempt, name='dispatch')
class EventDetailViewSet(viewsets.ReadOnlyModelViewSet):
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
class EventDetailAPIViewSet(viewsets.ModelViewSet):
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
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # 権限チェック
        if not request.user.is_superuser:
            if not instance.event.community.is_manager(request.user):
                return Response(
                    {"エラー": "このイベント詳細を削除する権限がありません。"},
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
class RecurrenceRuleViewSet(viewsets.ReadOnlyModelViewSet):
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
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
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

# Create your views here.
from corsheaders.middleware import CorsMiddleware
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
from event.models import Event, EventDetail
from .authentication import APIKeyAuthentication
from .serializers import CommunitySerializer, EventSerializer, EventDetailSerializer, EventDetailWriteSerializer


class CORSMixin:
    @classmethod
    def as_view(cls, *args, **kwargs):
        view = super().as_view(*args, **kwargs)
        return CorsMiddleware(view)


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
class CommunityViewSet(CORSMixin, viewsets.ReadOnlyModelViewSet):
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
class EventViewSet(CORSMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.filter(
        date__gte=timezone.now().date(),
        community__status='approved'
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
class EventDetailViewSet(CORSMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EventDetail.objects.filter(
        event__community__status='approved'
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
        return EventDetail.objects.filter(
            event__community__custom_user=user
        ).select_related('event', 'event__community')
    
    def perform_create(self, serializer):
        serializer.save()
        
    def perform_update(self, serializer):
        serializer.save()
        
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        
        # 権限チェック
        if not request.user.is_superuser:
            if instance.event.community.custom_user != request.user:
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

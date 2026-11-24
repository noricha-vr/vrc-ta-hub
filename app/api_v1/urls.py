from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BotInstallationAPIView, BotInstallationStatusAPIView,
    CommunityByDiscordAPIView, CommunityViewSet, EventViewSet, EventDetailViewSet,
    EventDetailAPIViewSet, EventDetailReviewAPIView, RecurrenceRuleViewSet,
)
from .recurrence_preview import RecurrencePreviewAPIView
from .recording_ops_views import (
    RecordingEditOrderClaimAPIView,
    RecordingEditOrderCompleteAPIView,
    RecordingEditOrderCreateAPIView,
    RecordingProxyDownloadUrlAPIView,
    RecordingProxyUploadUrlAPIView,
    RecordingReviewItemUpsertAPIView,
)

router = DefaultRouter()
router.register(r'community', CommunityViewSet)
router.register(r'event', EventViewSet)
router.register(r'event_detail', EventDetailViewSet)
router.register(r'event-details', EventDetailAPIViewSet, basename='event-detail-api')
router.register(r'recurrence-rules', RecurrenceRuleViewSet, basename='recurrence-rule')

urlpatterns = [
    path('event-details/<int:pk>/review/', EventDetailReviewAPIView.as_view(), name='event-detail-api-review'),
    path('community-by-discord/', CommunityByDiscordAPIView.as_view(), name='community-by-discord'),
    path('bot-installation/', BotInstallationAPIView.as_view(), name='bot-installation'),
    path('bot-installation/status/', BotInstallationStatusAPIView.as_view(), name='bot-installation-status'),
    path('recurrence-preview/', RecurrencePreviewAPIView.as_view(), name='recurrence-preview'),
    path(
        'recording-ops/items/<int:detail_id>/proxy-upload-url/',
        RecordingProxyUploadUrlAPIView.as_view(),
        name='recording-ops-proxy-upload-url',
    ),
    path(
        'recording-ops/items/<int:detail_id>/',
        RecordingReviewItemUpsertAPIView.as_view(),
        name='recording-ops-item-upsert',
    ),
    path(
        'recording-ops/items/<int:detail_id>/proxy-url/',
        RecordingProxyDownloadUrlAPIView.as_view(),
        name='recording-ops-proxy-url',
    ),
    path(
        'recording-ops/items/<int:detail_id>/orders/',
        RecordingEditOrderCreateAPIView.as_view(),
        name='recording-ops-order-create',
    ),
    path(
        'recording-ops/orders/claim/',
        RecordingEditOrderClaimAPIView.as_view(),
        name='recording-ops-order-claim',
    ),
    path(
        'recording-ops/orders/<int:order_id>/complete/',
        RecordingEditOrderCompleteAPIView.as_view(),
        name='recording-ops-order-complete',
    ),
] + router.urls

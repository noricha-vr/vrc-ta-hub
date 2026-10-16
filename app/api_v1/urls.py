from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CommunityByDiscordAPIView, CommunityViewSet, EventViewSet, EventDetailViewSet,
    EventDetailAPIViewSet, RecurrenceRuleViewSet,
)
from .recurrence_preview import RecurrencePreviewAPIView

router = DefaultRouter()
router.register(r'community', CommunityViewSet)
router.register(r'event', EventViewSet)
router.register(r'event_detail', EventDetailViewSet)
router.register(r'event-details', EventDetailAPIViewSet, basename='event-detail-api')
router.register(r'recurrence-rules', RecurrenceRuleViewSet, basename='recurrence-rule')

urlpatterns = [
    path('community-by-discord/', CommunityByDiscordAPIView.as_view(), name='community-by-discord'),
    path('recurrence-preview/', RecurrencePreviewAPIView.as_view(), name='recurrence-preview'),
] + router.urls

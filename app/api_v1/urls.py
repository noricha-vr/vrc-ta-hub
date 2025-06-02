from rest_framework.routers import DefaultRouter

from .views import CommunityViewSet, EventViewSet, EventDetailViewSet, EventDetailAPIViewSet

router = DefaultRouter()
router.register(r'community', CommunityViewSet)
router.register(r'event', EventViewSet)
router.register(r'event_detail', EventDetailViewSet)
router.register(r'event-details', EventDetailAPIViewSet, basename='event-detail-api')

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import CommunityViewSet, EventViewSet, EventDetailViewSet

router = DefaultRouter()
router.register(r'community', CommunityViewSet)
router.register(r'event', EventViewSet)
router.register(r'event_detail', EventDetailViewSet)

urlpatterns = router.urls

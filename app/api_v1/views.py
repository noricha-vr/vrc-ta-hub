# Create your views here.
from corsheaders.middleware import CorsMiddleware
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from community.models import Community
from event.models import Event, EventDetail
from .serializers import CommunitySerializer, EventSerializer, EventDetailSerializer


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


@method_decorator(csrf_exempt, name='dispatch')
class CommunityViewSet(CORSMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Community.objects.filter(end_at__isnull=True).order_by('-pk')
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


class EventViewSet(CORSMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.filter(date__gte=timezone.now().date()).select_related('community').order_by('date',
                                                                                                          'start_time')
    serializer_class = EventSerializer
    filterset_class = EventFilter
    filter_backends = [DjangoFilterBackend]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]


class EventDetailFilter(filters.FilterSet):
    theme = filters.CharFilter(lookup_expr='icontains')
    speaker = filters.CharFilter(lookup_expr='icontains')
    start_date = filters.DateFilter(field_name='event__date')
    start_time = filters.TimeFilter(field_name='start_time')

    class Meta:
        model = EventDetail
        fields = ['theme', 'speaker', 'start_date', 'start_time']


class EventDetailViewSet(CORSMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EventDetail.objects.select_related('event').filter(event__date__gte=timezone.now().date()).order_by(
        '-event__date', '-start_time')
    serializer_class = EventDetailSerializer
    filterset_class = EventDetailFilter
    filter_backends = [DjangoFilterBackend]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]

from django.urls import path
from django.views.generic import TemplateView

from .views import EventListView, EventDetailView, sync_calendar_events, EventDetailUpdateView, EventDetailCreateView, \
    EventDetailDeleteView, EventMyList, GenerateBlogView, EventDetailPastList, \
    EventDateUpdateView, EventDeleteView, GoogleCalendarEventCreateView, EventLogListView, LTApplicationCreateView, LTApplicationReviewView, \
    LTApplicationApproveView, LTApplicationCompleteView, LTApplicationRejectView
from .views.material_upload_reminder import send_material_upload_reminders_view
from .views.slide_reminder import send_slide_reminders
from .views_llm_generate import generate_llm_events

app_name = 'event'
urlpatterns = [
    path('list/', EventListView.as_view(), name='list'),
    path('<int:pk>/date/update/', EventDateUpdateView.as_view(), name='date_update'),
    path('delete/<int:pk>/', EventDeleteView.as_view(), name='delete'),
    path('my_list/', EventMyList.as_view(), name='my_list'),
    path('sync/', sync_calendar_events, name='sync_calendar_events'),
    path('calendar/create/', GoogleCalendarEventCreateView.as_view(), name='calendar_create'),
    # detail
    path('<int:event_pk>/detail/create/', EventDetailCreateView.as_view(), name='detail_create'),
    path('detail/<int:pk>/', EventDetailView.as_view(), name='detail'),
    path('detail/<int:pk>/update/', EventDetailUpdateView.as_view(), name='detail_update'),
    path('detail/<int:pk>/delete/', EventDetailDeleteView.as_view(), name='detail_delete'),
    path('detail/history/', EventDetailPastList.as_view(), name='detail_history'),
    path('event_log/', EventLogListView.as_view(), name='event_log_list'),
    path('generate_blog/<int:pk>/', GenerateBlogView.as_view(), name='generate_blog'),
    path('send-material-upload-reminders/', send_material_upload_reminders_view, name='send_material_upload_reminders'),
    path('send-slide-reminders/', send_slide_reminders, name='send_slide_reminders'),
    path('markdown/', TemplateView.as_view(template_name='event/markdown.html'), name='markdown'),
    path('generate/', generate_llm_events, name='generate_llm_events'),
    # LT申請
    path('apply/<int:community_pk>/', LTApplicationCreateView.as_view(), name='lt_application_create'),
    path('apply/<int:community_pk>/complete/', LTApplicationCompleteView.as_view(), name='lt_application_complete'),
    path('application/<int:pk>/review/', LTApplicationReviewView.as_view(), name='lt_application_review'),
    path('application/<int:pk>/approve/', LTApplicationApproveView.as_view(), name='lt_application_approve'),
    path('application/<int:pk>/reject/', LTApplicationRejectView.as_view(), name='lt_application_reject'),
]

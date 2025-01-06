from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from .forms import CalendarEntryForm
from .models import Community, CalendarEntry


class CalendarEntryUpdateView(LoginRequiredMixin, UpdateView):
    model = CalendarEntry
    form_class = CalendarEntryForm
    template_name = 'event_calendar/calendar_entry_form.html'
    success_url = reverse_lazy('account:settings')

    def test_func(self):
        calendar_entry = self.get_object()
        return self.request.user == calendar_entry.community.custom_user

    def get_object(self, queryset=None):
        community = Community.objects.get(pk=self.kwargs['pk'])
        calendar_entry, created = CalendarEntry.objects.get_or_create(
            community=community,
            defaults={
                'event_detail': community.description,
            }
        )
        return calendar_entry

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'VRCイベントカレンダー情報が更新されました。')
        return response

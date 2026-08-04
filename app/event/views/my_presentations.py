"""ログイン中ユーザーの発表一覧を表示する。"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from event.models import EventDetail


class MyPresentationsView(LoginRequiredMixin, ListView):
    """ログイン中ユーザーが編集できる承認済み発表を一覧表示する。"""

    model = EventDetail
    template_name = "event/my_presentations.html"
    context_object_name = "presentations"

    def get_queryset(self):
        """承認済みの本人申請発表を開催日の新しい順に返す。"""
        return (
            EventDetail.objects.filter(
                applicant=self.request.user,
                detail_type="LT",
                status="approved",
            )
            .select_related("event", "event__community")
            .order_by("-event__date", "start_time")
        )

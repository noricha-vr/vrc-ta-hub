"""旧自分の発表ページから発表一覧へ誘導する。"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.views.generic import RedirectView

class MyPresentationsView(LoginRequiredMixin, RedirectView):
    """ログイン後、発表一覧の本人絞り込みへリダイレクトする。"""

    def get_redirect_url(self, *args, **kwargs):
        return f"{reverse('event:detail_history')}?mine=1"

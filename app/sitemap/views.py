from django.views.generic import TemplateView
from event.models import EventDetail
from community.models import Community
from vket.models import VketCollaboration
from guide.views import get_flat_nav_items, load_navigation


class SitemapView(TemplateView):
    template_name = 'sitemap/sitemap.xml'
    content_type = 'application/xml'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_details = EventDetail.objects.filter(status='approved').order_by('-pk')
        context['event_details'] = event_details
        communities = Community.objects.filter(status='approved').order_by('-pk')
        context['communities'] = communities
        context['vket_collaborations'] = VketCollaboration.objects.exclude(phase=VketCollaboration.Phase.DRAFT)
        # ガイドのトップは /guide/ で出す（index は同じ内容の別 URL）
        context['guide_paths'] = [item.path for item in get_flat_nav_items(load_navigation()) if item.path != 'index']
        context['base_url'] = f'https://{self.request.get_host()}/'
        return context

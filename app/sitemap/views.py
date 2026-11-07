from django.views.generic import TemplateView
from event.models import EventDetail
from community.models import Community
from vket.models import VketCollaboration
from news.models import Post
from guide.lastmod import lastmod_for, latest_lastmod, load_records
from guide.views import get_flat_nav_items, load_navigation


class SitemapView(TemplateView):
    template_name = 'sitemap/sitemap.xml'
    content_type = 'application/xml'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_details = EventDetail.objects.filter(status='approved').order_by('-pk')
        context['event_details'] = event_details
        communities = Community.objects.filter(status='approved', is_listed=True).order_by('-pk')
        context['communities'] = communities
        context['vket_collaborations'] = VketCollaboration.objects.exclude(phase=VketCollaboration.Phase.DRAFT)
        context['news_posts'] = Post.objects.filter(is_published=True).order_by('-published_at', '-created_at')
        records = load_records()
        # /guide/（入口）と /guide/index/（はじめに）は別内容のページなので、どちらも載せる
        context['guide_top_lastmod'] = latest_lastmod(records)
        context['guide_pages'] = [
            {'path': item.path, 'lastmod': lastmod_for(item.path, records)}
            for item in get_flat_nav_items(load_navigation())
        ]
        context['base_url'] = f'https://{self.request.get_host()}/'
        return context

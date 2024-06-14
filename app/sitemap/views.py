from django.views.generic import ListView, TemplateView, RedirectView
from event.models import EventDetail
from community.models import Community
from django.views.static import serve


class SitemapView(TemplateView):
    template_name = 'sitemap/sitemap.xml'
    content_type = 'application/xml'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_details = EventDetail.objects.all().order_by('-pk')
        context['event_details'] = event_details
        communities = Community.objects.all().order_by('-pk')
        context['communities'] = communities
        context['base_url'] = f'https://{self.request.get_host()}/'
        return context

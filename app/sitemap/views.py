from django.views.generic import TemplateView
from event.models import EventDetail
from community.models import Community
from vket.models import VketCollaboration


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
        context['base_url'] = f'https://{self.request.get_host()}/'
        return context

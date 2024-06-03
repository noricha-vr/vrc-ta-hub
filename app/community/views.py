from django.shortcuts import render

# Create your views here.
# views.py
from django.views.generic import TemplateView, ListView, DetailView
from .models import Community


class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for community in context['communities']:
            if community.twitter_hashtag:
                community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                              tag.strip()]
            # join type
            vrc_group = community.vrchat_group
            if vrc_group.find('group/') != -1:
                community.join_type = 'group'
            elif vrc_group.find('/user/') != -1:
                community.join_type = 'user_page'
            elif vrc_group.find('vrch.at/') != -1:
                community.join_type = 'world'
            else:
                community.join_type = 'user_name'
        return context


class CommunityDetailView(DetailView):
    model = Community
    template_name = 'community/detail.html'
    context_object_name = 'community'

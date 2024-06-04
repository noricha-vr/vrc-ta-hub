from django.db.models import Q
from django.shortcuts import render

# Create your views here.
# views.py
from django.views.generic import TemplateView, ListView, DetailView

from .libs import get_join_type
from .models import Community

from .forms import CommunitySearchForm


class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            query = form.cleaned_data['query']
            weekdays = form.cleaned_data['weekdays']
            if query:
                queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
            if weekdays:
                queryset = queryset.filter(weekday__in=weekdays)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = CommunitySearchForm(self.request.GET)
        context['form'] = form
        context['search_count'] = self.get_queryset().count()
        for community in context['communities']:
            if community.twitter_hashtag:
                community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                              tag.strip()]
            community.join_type = get_join_type(community.vrchat_group)
        return context


class CommunityDetailView(DetailView):
    model = Community
    template_name = 'community/detail.html'
    context_object_name = 'community'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = context['community']
        if community.twitter_hashtag:
            community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                          tag.strip()]
        community.join_type = get_join_type(community.vrchat_group)
        return context

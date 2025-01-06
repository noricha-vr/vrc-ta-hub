# twitter/views.py
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView, ListView, DeleteView, TemplateView

from community.models import Community
from event.models import Event
from .forms import TwitterTemplateForm
from .models import TwitterTemplate
from .utils import format_event_info, generate_tweet, generate_tweet_url


class TwitterTemplateBaseView(LoginRequiredMixin, UserPassesTestMixin):
    model = TwitterTemplate
    form_class = TwitterTemplateForm
    template_name = 'twitter/twitter_template_form.html'

    def test_func(self):
        community = get_object_or_404(Community, custom_user=self.request.user)
        return community is not None

    def get_success_url(self):
        return reverse_lazy('twitter:template_list')

    def form_valid(self, form):
        form.instance.community = get_object_or_404(Community, custom_user=self.request.user)
        return super().form_valid(form)


class TwitterTemplateCreateView(TwitterTemplateBaseView, CreateView):
    pass


class TwitterTemplateUpdateView(TwitterTemplateBaseView, UpdateView):
    def test_func(self):
        if not super().test_func():
            return False
        twitter_template = self.get_object()
        return twitter_template.community.custom_user == self.request.user


class TwitterTemplateListView(LoginRequiredMixin, ListView):
    model = TwitterTemplate
    template_name = 'twitter/twitter_template_list.html'
    context_object_name = 'templates'

    def get_queryset(self):
        community = get_object_or_404(Community, custom_user=self.request.user)
        return TwitterTemplate.objects.filter(community=community)


class TweetEventView(View):
    def get(self, request, event_pk, template_pk):
        event = get_object_or_404(Event, pk=event_pk)
        template = get_object_or_404(TwitterTemplate, pk=template_pk, community=event.community)
        tweet_url = generate_tweet_url(event, template)
        return redirect(tweet_url)


class TwitterTemplateDeleteView(LoginRequiredMixin, DeleteView):
    model = TwitterTemplate
    success_url = reverse_lazy('twitter:template_list')

    def form_valid(self, form):
        success_url = self.get_success_url()
        self.object.delete()
        messages.success(self.request, 'テンプレートが削除されました。')
        return JsonResponse({'success': True})

    def delete(self, request, *args, **kwargs):
        """
        FormMixinを使用するため、deleteメソッドをオーバーライドして
        form_validメソッドを呼び出します。
        """
        self.object = self.get_object()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)


# twitter/views.py


class TweetEventWithTemplateView(TemplateView):
    template_name = 'twitter/tweet_preview.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = get_object_or_404(Event, pk=self.kwargs['event_pk'])
        template = get_object_or_404(TwitterTemplate, pk=self.kwargs['template_pk'])

        # Format event info before generating tweet
        event_info = format_event_info(event)
        tweet_text = generate_tweet(template.template, event_info)
        
        context.update({
            'tweet_text': tweet_text,
            'event': event,
            'template': template,
        })
        return context

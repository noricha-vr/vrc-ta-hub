# twitter/views.py
import logging
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView, ListView, DeleteView, TemplateView

logger = logging.getLogger(__name__)

from community.models import Community
from event.models import Event
from .forms import TwitterTemplateForm
from .models import TwitterTemplate
from .utils import format_event_info, generate_tweet, generate_tweet_url


class TwitterTemplateBaseView(LoginRequiredMixin, UserPassesTestMixin):
    model = TwitterTemplate
    form_class = TwitterTemplateForm
    template_name = 'twitter/twitter_template_form.html'

    def get_active_community(self):
        """セッションからアクティブな集会を取得"""
        community_id = self.request.session.get('active_community_id')
        if not community_id:
            return None
        community = Community.objects.filter(id=community_id).first()
        if community and community.is_manager(self.request.user):
            return community
        return None

    def test_func(self):
        community = self.get_active_community()
        return community is not None

    def get_success_url(self):
        return reverse_lazy('twitter:template_list')

    def form_valid(self, form):
        community = self.get_active_community()
        if not community:
            raise Http404("集会が選択されていないか、権限がありません")
        form.instance.community = community
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
        """セッションからactive_community_idを取得してテンプレートを絞り込む"""
        community_id = self.request.session.get('active_community_id')
        if not community_id:
            return TwitterTemplate.objects.none()

        community = get_object_or_404(Community, id=community_id)

        # メンバーシップ権限チェック
        if not community.is_manager(self.request.user):
            return TwitterTemplate.objects.none()

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
        
        # Add debug logging
        logger.debug(f"Generated tweet_text: {tweet_text}")
        
        # Replace newlines with HTML line breaks
        if tweet_text:
            tweet_text = tweet_text.replace('\n', '<br>')
        
        context.update({
            'tweet_text': tweet_text,
            'event': event,
            'template': template,
        })
        return context

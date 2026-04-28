import json
import logging
from datetime import timedelta
from typing import List, Dict

from django.core.cache import cache
from django.http import Http404
from django.views.generic import DetailView

from community.models import WEEKDAY_CHOICES
from event.libs import convert_markdown
from event.models import EventDetail
from event.views.helpers import can_manage_event_detail, extract_video_info
from utils.vrchat_time import get_vrchat_today

logger = logging.getLogger(__name__)


class EventDetailView(DetailView):
    model = EventDetail
    template_name = 'event/detail.html'
    context_object_name = 'event_detail'

    def get_queryset(self):
        return super().get_queryset().select_related('event__community')

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.status == 'approved':
            return obj
        user = self.request.user
        if user.is_authenticated:
            if user.is_superuser:
                return obj
            if obj.event.community.can_edit(user):
                return obj
            if obj.applicant and obj.applicant == user:
                return obj
        raise Http404

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_detail = self.object
        video_id, start_time = extract_video_info(event_detail.youtube_url)
        context['video_id'] = video_id
        context['start_time'] = start_time
        context['is_discord'] = event_detail.youtube_url.startswith(
            'https://discord.com/') if event_detail.youtube_url else False
        context['html_content'] = convert_markdown(event_detail.contents)
        context['related_event_details'] = self._fetch_related_event_details(event_detail)

        # コミュニティの開催情報を追加
        community = event_detail.event.community
        context['community_schedule'] = {
            'weekdays': [dict(WEEKDAY_CHOICES)[day] for day in community.weekdays],
            'start_time': community.start_time,
            'end_time': community.end_time,
            'frequency': community.frequency
        }

        # Twitterボタン表示用のフラグとテンプレートを追加
        today = get_vrchat_today()
        twitter_display_until = event_detail.event.date + timedelta(days=7)
        context['twitter_button_active'] = today <= twitter_display_until
        context['twitter_templates'] = event_detail.event.community.twitter_template.all()

        # ユーザーがログインしていて、このイベントの集会管理者であるか確認
        if self.request.user.is_authenticated and event_detail.event.community.can_edit(self.request.user):
            context['is_community_owner'] = True
        else:
            context['is_community_owner'] = False
        context['can_manage_event_detail'] = can_manage_event_detail(
            self.request.user,
            event_detail,
        )

        # 構造化データ（BlogPosting）を生成（ブログ記事のみ）
        try:
            if event_detail.detail_type == 'BLOG':
                request = self.request
                absolute_url = request.build_absolute_uri()

                images: List[str] = []
                # EventDetailのサムネイル画像
                try:
                    thumbnail = event_detail.thumbnail
                    if thumbnail and getattr(thumbnail, 'url', None):
                        images.append(request.build_absolute_uri(thumbnail.url))
                except Exception:
                    pass

                # コミュニティのポスター画像
                try:
                    poster = event_detail.event.community.poster_image
                    if poster and getattr(poster, 'url', None):
                        images.append(request.build_absolute_uri(poster.url))
                except Exception:
                    pass

                # YouTubeサムネイル
                if context.get('video_id'):
                    images.append(f"https://img.youtube.com/vi/{context['video_id']}/hqdefault.jpg")

                # 著者情報（発表者がいればPerson、いなければコミュニティ名のOrganization）
                if event_detail.speaker:
                    author_obj: Dict = {"@type": "Person", "name": event_detail.speaker}
                else:
                    author_obj = {"@type": "Organization", "name": event_detail.event.community.name}

                # パブリッシャ（コミュニティ）
                publisher_obj: Dict = {
                    "@type": "Organization",
                    "name": event_detail.event.community.name,
                }
                if images:
                    # ロゴとして最初の画像を使用（適切なロゴがない場合のフォールバック）
                    publisher_obj["logo"] = {"@type": "ImageObject", "url": images[0]}

                # メタディスクリプションのフォールバック
                description = (event_detail.meta_description or event_detail.theme or event_detail.title or "").strip()

                structured_data: Dict = {
                    "@context": "https://schema.org",
                    "@type": "BlogPosting",
                    "mainEntityOfPage": {"@type": "WebPage", "@id": absolute_url},
                    "headline": event_detail.title or "",
                    "url": absolute_url,
                    "inLanguage": "ja-JP",
                    "isAccessibleForFree": True,
                    "datePublished": event_detail.created_at.isoformat(),
                    "dateModified": event_detail.updated_at.isoformat(),
                    "description": description,
                    "publisher": publisher_obj,
                    "author": author_obj,
                }

                if images:
                    structured_data["image"] = images

                # 可能なら本文も追加（長すぎる場合はカット）
                max_article_body_length = 10000
                if event_detail.contents:
                    body_text = event_detail.contents
                    if len(body_text) > max_article_body_length:
                        body_text = body_text[:max_article_body_length]
                    structured_data["articleBody"] = body_text

                structured_data_json = json.dumps(structured_data, ensure_ascii=False)
                structured_data_json = structured_data_json.replace("</", "<\\/")
                context['structured_data_json'] = structured_data_json
                logger.info(f"Structured data prepared for EventDetail(BLOG): id={event_detail.id}")
        except Exception as e:
            logger.warning(f"Failed to prepare structured data for EventDetail id={event_detail.id}: {str(e)}")

        return context

    def _fetch_related_event_details(self, event_detail: EventDetail) -> List[EventDetail]:
        # キャッシュキーを生成
        cache_key = f'related_event_details_{event_detail.event_id}'
        related_event_details = cache.get(cache_key)
        if related_event_details is None:
            max_related_items = 6
            cache_timeout_seconds = 60 * 60
            # キャッシュがない場合のみDBクエリを実行
            related_event_details = list(
                EventDetail.objects
                .filter(
                    event__community=event_detail.event.community,
                    status='approved',
                    h1__isnull=False,
                    h1__gt=''  # より効率的な空文字列の除外
                )
                .exclude(id=event_detail.id)
                .order_by('-created_at')
                .values('id', 'h1')[:max_related_items]
            )

            # 1時間キャッシュする
            cache.set(cache_key, related_event_details, cache_timeout_seconds)

        return related_event_details

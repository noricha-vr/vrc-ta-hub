"""キャンペーン管理 view（一覧 / 作成 / 編集 / 削除）。

権限境界:
- 全ての view は LoginRequiredMixin + accessible_community_ids でフィルタする
- 他集会の Campaign は一覧に出ず、直接 URL アクセスは 404 を返す
- フォームの community 選択肢もサーバー側で絞る（HTML 改ざん耐性）
"""
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import CampaignForm
from .models import Campaign
from .qr_generator import generate_qr_png
from .services import accessible_community_ids

logger = logging.getLogger('analytics')


class _AccessibleCampaignMixin(LoginRequiredMixin):
    """自分がアクセス可能な community の Campaign に限定する共通 mixin。"""

    def get_queryset(self):
        ids = accessible_community_ids(self.request.user)
        return (
            Campaign.objects
            .filter(community_id__in=ids)
            .select_related('community')
        )


class CampaignListView(_AccessibleCampaignMixin, ListView):
    template_name = 'analytics/campaign_list.html'
    context_object_name = 'campaigns'
    paginate_by = 50


class CampaignFormMixin:
    """作成・編集で共有するフォーム処理。

    form_valid で url を再計算し、QR PNG を生成して qr_image に保存する。
    """

    form_class = CampaignForm
    template_name = 'analytics/campaign_form.html'
    success_url = reverse_lazy('analytics:campaign_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def _regenerate_qr_if_needed(self, campaign: Campaign, force: bool):
        """force=True または qr_image 未設定なら QR を再生成する。"""
        if force or not campaign.qr_image:
            png = generate_qr_png(campaign.url)
            # 既存ファイルは upload_to の uuid 命名で衝突しないが、不要なら save=False で更新
            campaign.qr_image.save(png.name, png, save=True)
            logger.info(
                'Campaign QR regenerated: id=%s utm_campaign=%s',
                campaign.pk, campaign.utm_campaign,
            )


class CampaignCreateView(_AccessibleCampaignMixin, CampaignFormMixin, CreateView):
    model = Campaign

    def form_valid(self, form):
        # community を accessible_community_ids 内に限定（form の queryset 制約と二重防御）
        community = form.cleaned_data.get('community')
        if community.id not in accessible_community_ids(self.request.user):
            form.add_error('community', 'この集会には Campaign を作成できません。')
            return self.form_invalid(form)
        response = super().form_valid(form)
        self._regenerate_qr_if_needed(self.object, force=True)
        return response


class CampaignUpdateView(_AccessibleCampaignMixin, CampaignFormMixin, UpdateView):
    model = Campaign

    def form_valid(self, form):
        # 既存 utm 値と比較し、URL を変える編集のときだけ QR を再生成する
        old = self.get_object()
        url_changed = (
            old.utm_source != form.cleaned_data['utm_source']
            or old.utm_medium != form.cleaned_data['utm_medium']
            or old.utm_campaign != form.cleaned_data['utm_campaign']
            or old.landing_path != form.cleaned_data['landing_path']
        )
        response = super().form_valid(form)
        self._regenerate_qr_if_needed(self.object, force=url_changed)
        return response


class CampaignDeleteView(_AccessibleCampaignMixin, DeleteView):
    template_name = 'analytics/campaign_confirm_delete.html'
    success_url = reverse_lazy('analytics:campaign_list')

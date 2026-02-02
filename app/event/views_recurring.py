"""定期イベント関連のビュー"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from .forms import RecurringEventForm
from .models import RecurrenceRule
from .recurrence_service import RecurrenceService


@login_required
def create_recurring_event(request, community_id):
    """定期イベントの作成"""
    community = get_object_or_404(Community, id=community_id)

    # 権限チェック
    if not community.is_owner(request.user):
        messages.error(request, 'このコミュニティのイベントを作成する権限がありません。')
        return redirect('community:detail', community_id=community.id)
    
    if request.method == 'POST':
        form = RecurringEventForm(request.POST, community=community)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # RecurrenceRuleを作成
                    rule = RecurrenceRule.objects.create(
                        community=community,
                        frequency=form.cleaned_data['frequency'],
                        interval=form.cleaned_data['interval'],
                        week_of_month=form.cleaned_data.get('week_of_month'),
                        custom_rule=form.cleaned_data.get('custom_rule', ''),
                        end_date=form.cleaned_data.get('end_date')
                    )
                    
                    # RecurrenceServiceを使用してイベントを生成
                    service = RecurrenceService()
                    events = service.create_recurring_events(
                        community=community,
                        rule=rule,
                        base_date=form.cleaned_data['base_date'],
                        start_time=form.cleaned_data['start_time'],
                        duration=form.cleaned_data['duration'],
                        months=3  # デフォルト3ヶ月分生成
                    )
                    
                    if events:
                        messages.success(request, f'{len(events)}件の定期イベントを作成しました。')
                        # 最初のイベントの詳細ページへリダイレクト
                        return redirect('event:detail', event_id=events[0].id)
                    else:
                        messages.error(request, 'イベントの作成に失敗しました。')
            
            except Exception as e:
                messages.error(request, f'エラーが発生しました: {str(e)}')
    else:
        form = RecurringEventForm(community=community)
    
    context = {
        'form': form,
        'community': community,
    }
    return render(request, 'event/create_recurring.html', context)


@login_required
def list_recurring_events(request):
    """定期イベントの一覧表示"""
    # ユーザーが管理するコミュニティの定期イベント（マスター）を取得
    user_community_ids = request.user.community_memberships.values_list('community_id', flat=True)
    communities = Community.objects.filter(id__in=user_community_ids)
    recurring_masters = []
    
    for community in communities:
        masters = community.events.filter(
            is_recurring_master=True,
            recurrence_rule__isnull=False
        ).select_related('recurrence_rule').order_by('-date')
        
        for master in masters:
            # 今後のインスタンス数を計算
            upcoming_count = master.recurring_instances.filter(
                date__gte=timezone.now().date()
            ).count()
            
            recurring_masters.append({
                'master': master,
                'community': community,
                'upcoming_count': upcoming_count,
                'rule': master.recurrence_rule
            })
    
    context = {
        'recurring_masters': recurring_masters,
    }
    return render(request, 'event/list_recurring.html', context)
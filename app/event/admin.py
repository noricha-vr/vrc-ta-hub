from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from django.urls import path
from django.shortcuts import render, redirect
from django.utils import timezone
from .models import Event, EventDetail, RecurrenceRule


class EventDetailInline(admin.TabularInline):
    model = EventDetail
    extra = 1  # 追加フォームの数


@admin.register(RecurrenceRule)
class RecurrenceRuleAdmin(admin.ModelAdmin):
    list_display = ('community', 'frequency', 'interval', 'week_of_month', 'end_date', 'created_at', 'get_future_events_count', 'action_links')
    list_filter = ('frequency', 'created_at', 'community')
    search_fields = ('custom_rule', 'community__name')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('community',)
    
    def get_future_events_count(self, obj):
        """未来のイベント数を表示"""
        today = timezone.now().date()
        count = Event.objects.filter(
            recurrence_rule=obj,
            date__gte=today
        ).count()
        
        # インスタンスイベントも含める
        master_events = Event.objects.filter(
            recurrence_rule=obj,
            is_recurring_master=True
        )
        for master in master_events:
            count += master.recurring_instances.filter(date__gte=today).count()
        
        return count
    
    get_future_events_count.short_description = '未来のイベント数'
    
    def action_links(self, obj):
        """アクション列を表示"""
        if obj and hasattr(obj, 'pk'):
            return format_html(
                '<a class="button" href="{}">未来のイベントを削除</a>',
                f'/admin/event/recurrencerule/{obj.pk}/delete_future_events/'
            )
        return '-'
    
    action_links.short_description = 'アクション'
    action_links.allow_tags = True
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:pk>/delete_future_events/',
                self.admin_site.admin_view(self.delete_future_events_view),
                name='event_recurrencerule_delete_future_events',
            ),
        ]
        return custom_urls + urls
    
    def delete_future_events_view(self, request, pk):
        """未来のイベントを削除するビュー"""
        recurrence_rule = RecurrenceRule.objects.get(pk=pk)
        
        if request.method == 'POST':
            delete_from_date = request.POST.get('delete_from_date')
            if delete_from_date:
                delete_from_date = timezone.datetime.strptime(delete_from_date, '%Y-%m-%d').date()
            else:
                delete_from_date = timezone.now().date()
            
            deleted_count = recurrence_rule.delete_future_events(delete_from_date)
            
            messages.success(
                request,
                f'{deleted_count}件の未来のイベントを削除しました。'
            )
            
            if request.POST.get('delete_rule') == 'on':
                recurrence_rule.delete(delete_future_events=False)  # 既に削除済みなのでFalse
                messages.success(request, '定期ルールも削除しました。')
                return redirect('/admin/event/recurrencerule/')
            
            return redirect(f'/admin/event/recurrencerule/{pk}/change/')
        
        # 未来のイベントを取得して表示
        today = timezone.now().date()
        future_events = []
        
        # マスターイベントとインスタンスを取得
        master_events = Event.objects.filter(
            recurrence_rule=recurrence_rule,
            is_recurring_master=True
        )
        
        for master in master_events:
            if master.date >= today:
                future_events.append(master)
            future_events.extend(
                master.recurring_instances.filter(date__gte=today).order_by('date')
            )
        
        # 直接ルールに紐づくイベントも含める
        direct_events = Event.objects.filter(
            recurrence_rule=recurrence_rule,
            date__gte=today,
            is_recurring_master=False,
            recurring_master__isnull=True
        )
        future_events.extend(direct_events)
        
        # 日付でソート
        future_events.sort(key=lambda e: e.date)
        
        context = {
            'recurrence_rule': recurrence_rule,
            'future_events': future_events,
            'today': today,
            'opts': self.model._meta,
        }
        
        return render(request, 'admin/event/recurrencerule/delete_future_events.html', context)
    
    def delete_model(self, request, obj):
        """モデル削除時の処理"""
        # 未来のイベント数を取得
        future_count = self.get_future_events_count(obj)
        
        if future_count > 0:
            messages.warning(
                request,
                f'この定期ルールに関連する{future_count}件の未来のイベントも削除されます。'
            )
        
        super().delete_model(request, obj)
    
    def delete_queryset(self, request, queryset):
        """複数選択削除時の処理"""
        total_future_count = 0
        
        for obj in queryset:
            future_count = self.get_future_events_count(obj)
            total_future_count += future_count
        
        if total_future_count > 0:
            messages.warning(
                request,
                f'選択された定期ルールに関連する合計{total_future_count}件の未来のイベントも削除されます。'
            )
        
        super().delete_queryset(request, queryset)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('get_community_name', 'date', 'start_time', 'end_time', 'is_recurring_master', 'recurring_master')
    list_filter = ('date', 'is_recurring_master')
    search_fields = ('community__name', 'date')
    inlines = [EventDetailInline]
    raw_id_fields = ('recurring_master', 'recurrence_rule')

    def get_community_name(self, obj):
        return obj.community.name

    get_community_name.short_description = '集会名'


@admin.register(EventDetail)
class EventDetailAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'updated_at', 'detail_type', 'theme', 'speaker')
    list_filter = ('detail_type', 'theme', 'speaker')
    readonly_fields = ('event',)
    
    def formfield_for_dbfield(self, db_field, **kwargs):
        field = super().formfield_for_dbfield(db_field, **kwargs)
        if db_field.name == 'slide_file':
            field.help_text = 'PDFファイルのみアップロード可能です（最大30MB）'
            field.widget.attrs['accept'] = '.pdf'
        return field

from django.views.generic import ListView
from merdate.models import Search

class SitemapView(ListView):
    model = Search
    template_name = 'sitemap/sitemap.xml'
    content_type = 'application/xml'
    paginate_by = 10000

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_url'] = f'https://{self.request.get_host()}/'

        # 制御文字を削除する関数
        def remove_control_characters(s):
            return s.translate(dict.fromkeys(range(32)))

        # オブジェクトのフィールド値から制御文字を削除
        for obj in context['object_list']:
            obj.keyword = remove_control_characters(obj.keyword)
        return context

    def get_queryset(self):
        query_set = Search.objects.filter(count__gte=3,item_count__gte=1).order_by('-count')
        print('url count:',query_set.count())
        return query_set
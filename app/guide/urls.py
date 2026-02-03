from django.urls import path

from guide.views import GuideIndexView, GuidePageView

app_name = 'guide'

urlpatterns = [
    path('', GuideIndexView.as_view(), name='index'),
    path('<path:path>/', GuidePageView.as_view(), name='page'),
]

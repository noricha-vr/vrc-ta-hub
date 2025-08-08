from django.views.generic import ListView

from .models import Post


class PostListView(ListView):
    model = Post
    paginate_by = 10

    def get_queryset(self):
        return (
            Post.objects.select_related("category")
            .filter(is_published=True)
            .order_by("-published_at", "-created_at")
        )

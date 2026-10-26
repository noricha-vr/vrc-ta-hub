"""アカウント画面用の架空の表示名。共通の4ユーザーは増やさない。"""


def seed(context):
    for user in context['users'].values():
        user.display_name = user.user_name
        user.save(update_fields=['display_name'])

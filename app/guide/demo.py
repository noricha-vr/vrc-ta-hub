"""撮影専用の公開可能な架空アカウント。"""
import os
import secrets

DEMO_USERS = {
    'owner': ('さくら', 'sakura@example.com'),
    'staff': ('レン', 'ren@example.com'),
    'speaker': ('アオイ', 'aoi@example.com'),
    'general': ('ヒナ', 'hina@example.com'),
}


def demo_password() -> str:
    """撮影用の一時 DB だけで使う使い捨てのパスワード。

    run.sh が 1 回の撮影ごとに生成して GUIDE_DEMO_PASSWORD で渡す（データも DB も撮影の後に消える）。
    リポジトリに固定値を置かないのは、秘密検知に引っかかるのと、実在の資格情報と紛らわしいため。
    """
    return os.environ.get('GUIDE_DEMO_PASSWORD') or secrets.token_urlsafe(16)

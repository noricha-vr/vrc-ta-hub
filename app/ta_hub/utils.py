"""ta_hub 共通ユーティリティ"""

# X-Forwarded-For 内の最小 IP 数（クライアント + LB）
_MIN_IPS_WITH_LB = 2


def get_client_ip(request):
    """Cloud Run 配下を考慮してクライアント IP を取得する。

    Cloud Run では Google LB が X-Forwarded-For の末尾にクライアント IP を追加する。
    構成: [ユーザー偽装IP, ..., クライアントIP, Google LB IP]

    単一 IP の場合はそのまま返す。
    2つ以上の場合は末尾から2番目（= LB が追加したクライアント IP）を返す。

    Args:
        request: Django の HttpRequest オブジェクト

    Returns:
        クライアント IP アドレス文字列
    """
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        ips = [ip.strip() for ip in forwarded_for.split(',')]
        if len(ips) < _MIN_IPS_WITH_LB:
            return ips[0]
        return ips[-_MIN_IPS_WITH_LB]
    return request.META.get('REMOTE_ADDR') or '0.0.0.0'

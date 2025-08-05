from django.utils import timezone


def get_vrchat_today():
    """
    VRChatterの生活リズムに合わせた「今日」を返す
    朝4時を日付の境界とする
    
    Returns:
        date: VRChat時間での「今日」の日付
    """
    current_time = timezone.now()
    if current_time.hour < 4:
        # 朝4時前の場合は前日として扱う
        return (current_time - timezone.timedelta(days=1)).date()
    else:
        return current_time.date()
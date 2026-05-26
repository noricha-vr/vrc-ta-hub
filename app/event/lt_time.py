from datetime import datetime, timedelta


def calc_lt_start_time(event_start_time, offset_minutes):
    """集会の start_time に offset(分) を加算した time を返す。

    datetime.time は加算非対応のため、datetime.combine で日付を仮置きして演算する。
    23:50 + 30分 → 00:20 のように 24h を跨ぐケースも循環する（Community.end_time と同じ慣例）。
    申請フォームのラベル表示と View の保存ロジックで同じ結果を保証するための共通ヘルパー。
    """
    base = datetime.combine(datetime.today(), event_start_time)
    return (base + timedelta(minutes=offset_minutes)).time()

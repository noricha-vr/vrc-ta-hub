import sys
import django
from django.conf import settings
from datetime import date

# Django setup if run standalone (though usually run via manage.py shell)
# if not settings.configured:
#    ... (assumes manage.py shell context)

from event.models import EventDetail

def main():
    # Filter for December 2025 events
    # Adjust dates strictly if needed, e.g. 2025-12-06 to 2025-12-21 based on twitter post "16 days"
    # But filtering whole month is safer unless there are other events.
    start_date = date(2025, 12, 6)
    end_date = date(2025, 12, 21)

    details = EventDetail.objects.filter(
        event__date__range=(start_date, end_date)
    ).select_related('event', 'event__community').order_by('event__date', 'start_time')

    if not details.exists():
        print("No events found for Dec 2025.")
        return

    # Valid speakers/titles allowlist from CSV
    # Using a mix of speaker name or title keywords if speaker is missing in CSV (some are empty)
    # CSV Data Summary:
    # 12/6: ITインフラ集会 (Talk show) - No speaker listed in CSV, but title "トークショー" likely match? 
    #       Actually CSV says "要確認" (Needs Confirmation) for task, but title is empty in col 5?
    #       Wait, CSV: ITインフラ集会,12/6,22:00,,,要確認  <- Speaker empty, Theme empty
    #       But draft has: "トークショー" by "えー,kimkim0106..."
    #       The schedule image likely has "ITインフラ集会".
    #       User said "exclude events NOT in the list".
    #       So if it's in the CSV (even with empty speaker), we should include the event from DB if it matches Community/Date.
    
    # Let's verify what to filter.
    # CSV has:
    # ITインフラ集会 (12/6)
    # VRC微分音集会 (12/7)
    # VRC-AI集会 (12/8) -> DB might match "AI集会" or "AI集会テックWeek"?
    # C# Tokyo VRもくもく会 (12/9)
    # ML集会 (12/10)
    # Web技術集会 (12/10)
    # UIUXデザイン集会 (12/11)
    # DS集会 (12/11) -> DB "データサイエンティスト集会"?
    # エンジニア集会 (12/12) -> DB "エンジニア作業飲み集会"?
    # VRChat物理学集会 (12/13)
    # バックエンド集会 (12/13)
    # ITキャリア相談 (12/14) -> DB "ITエンジニア キャリア相談・雑談集会"?
    # VR酔い訓練集会 (12/15)
    # CS集会 (12/16)
    # ブラックホール集会 (12/17) -> Not in draft?
    # 妖怪好き交流所 (12/17)
    # 個人開発集会 (12/18)
    # 分解技術集会 (12/18)
    # VFN核融合 (12/19) -> DB "VR核融合コミュニティ..."
    # 分散システム集会 (12/20)
    # セキュリティ集会 (12/20)
    # 計算と自然集会 (12/20) -> DB "「計算と自然」集会"
    # VRC脳波技術集会 (12/21)

    # Strategy: Filter by Community Name (fuzzy match) AND Date.
    # List of valid communities/dates from CSV:
    valid_events = [
        ("ITインフラ集会", 6),
        ("VRC微分音集会", 7),
        ("AI集会", 8),
        ("C# Tokyo", 9),
        ("ML集会", 10),
        ("Web技術集会", 10),
        ("UIUXデザイン集会", 11),
        ("データサイエンティスト集会", 11), # DS集会
        ("エンジニア作業飲み集会", 12), # エンジニア集会
        ("VRChat物理学集会", 13),
        ("バックエンド集会", 13),
        ("ITエンジニア キャリア相談", 14), # ITキャリア相談
        ("VR酔い訓練集会", 15),
        ("CS集会", 16),
        # ("ブラックホール集会", 17), # DB draft didn't show this, maybe not in DB?
        ("妖怪好き交流所", 17),
        ("個人開発集会", 18),
        ("分解技術集会", 18),
        ("VR核融合コミュニティ", 19), # VFN核融合
        ("分散システム集会", 20),
        ("セキュリティ集会", 20),
        ("計算と自然", 20),
        ("VRC脳波技術集会", 21),
    ]

    print("## 登壇イベント一覧\n")
    
    current_date = None

    for detail in details:
        event = detail.event
        community = event.community
        
        # Check if this event matches our valid list
        is_valid = False
        for v_name, v_day in valid_events:
            if event.date.day == v_day and v_name in community.name:
                is_valid = True
                break
        
        if not is_valid:
            continue

        # New Date Header
        if current_date != event.date:
            current_date = event.date
            # Format: 12月6日 (土)
            # weekday is stored as 'Sun', 'Mon' etc in model choices usually, or computed
            w_list = ['月', '火', '水', '木', '金', '土', '日']
            w_idx = event.date.weekday()
            w_str = w_list[w_idx]
            print(f"### {event.date.month}月{event.date.day}日 ({w_str})\n")

        # Event Block
        # Using detail.title (h1 or theme), detail.speaker
        title = detail.title
        speaker = detail.speaker if detail.speaker else "発表者未定"
        
        # Checking for YouTube URL
        youtube_block = ""
        if detail.youtube_url:
            # If we want to embed, we might use HTML. 
            # Or just a link. The previous page used standard markdown likely.
            # If the user wants an embedded player, we usually need custom HTML or shortcode.
            # Assuming standard link for now or simple HTML embed if ID exists.
            vid = detail.video_id
            if vid:
                 youtube_block = f'<div class="youtube-embed-container"><iframe src="https://www.youtube.com/embed/{vid}" frameborder="0" allowfullscreen></iframe></div>\n'
            else:
                 youtube_block = f"[動画を見る]({detail.youtube_url})\n"
        else:
            youtube_block = "<!-- 動画URL待ち -->\n"

        print(f"#### {community.name}")
        print(f"**{title}**")
        print(f"登壇者: {speaker}\n")
        print(youtube_block)
        print("---\n")

# Call main directly as we are piping to shell
print("Script starting...")
print(f"Total EventDetails in DB: {EventDetail.objects.count()}")
main()

"""お知らせのサムネイル（リッチ版・サブなし）を作り、機械チェックの結果を check.json に残す。

右側の素材は切り取らずに枠へ収める（contain）。どこを見せるかは素材側の crop で決める。
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import argparse
import json
import math
from pathlib import Path

W, H = 1200, 630


def F(w, s):
    return ImageFont.truetype(f"/System/Library/Fonts/ヒラギノ角ゴシック W{w}.ttc", s)


TEXT_X, PANEL_X, PANEL_Y, PANEL_W, PANEL_H = 60, 616, 56, 548, 520
TEXT_W = PANEL_X - TEXT_X - 36


def load(name, crop=None):
    im = Image.open(f"{S}/{name}").convert("RGB")
    if crop:  # 0〜1 の比率で指定
        x0, y0, x1, y1 = crop
        im = im.crop((int(x0 * im.width), int(y0 * im.height), int(x1 * im.width), int(y1 * im.height)))
    return im


def contain(im, w, h):
    r = min(w / im.width, h / im.height)
    return im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)


def rounded_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, *size), radius=r, fill=255)
    return m


def shadowed(bg, im, xy, r=16):
    im = im.convert("RGBA")
    im.putalpha(rounded_mask(im.size, r))
    sh = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        (xy[0] + 6, xy[1] + 12, xy[0] + im.width + 6, xy[1] + im.height + 12), radius=r, fill=(0, 0, 0, 70)
    )
    bg.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14)))
    bg.alpha_composite(im, xy)
    return (xy[0], xy[1], xy[0] + im.width, xy[1] + im.height)


def browser(im, url):
    """サイトの画面にはブラウザの枠を付けて「Web の画面」と分かるようにする。"""
    bar = 34
    out = Image.new("RGB", (im.width, im.height + bar), "#e9edf2")
    d = ImageDraw.Draw(out)
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((14 + i * 20, 12, 26 + i * 20, 24), fill=c)
    d.rounded_rectangle((84, 7, min(im.width - 14, 84 + 360), 27), radius=10, fill="#ffffff")
    d.text((98, 9), url, font=F(4, 13), fill="#5b6670")
    out.paste(im, (0, bar))
    return out


def play_icon(d, cx, cy, r, fill="#ff0033"):
    d.rounded_rectangle((cx - r * 1.4, cy - r, cx + r * 1.4, cy + r), radius=r * 0.45, fill=fill)
    d.polygon([(cx - r * 0.45, cy - r * 0.55), (cx - r * 0.45, cy + r * 0.55), (cx + r * 0.6, cy)], fill="#ffffff")


def place_center(bg, im, r=16):
    x = PANEL_X + (PANEL_W - im.width) // 2
    y = PANEL_Y + (PANEL_H - im.height) // 2
    return shadowed(bg, im, (x, y), r)


def panel(bg, spec):
    kind = spec["kind"]
    if kind == "single":
        im = load(spec["src"], spec.get("crop"))
        if spec.get("url"):
            im = browser(contain(im, PANEL_W, PANEL_H - 34), spec["url"])
        return [place_center(bg, contain(im, PANEL_W, PANEL_H))]
    if kind == "grid4":
        gap = 10
        cw = (PANEL_W - gap) // 2
        ch = int(cw * 9 / 16)
        tot_h = ch * 2 + gap
        x0 = PANEL_X
        y0 = PANEL_Y + (PANEL_H - tot_h) // 2
        boxes = []
        for i, n in enumerate(spec["srcs"][:4]):
            im = load(n).resize((cw, ch), Image.LANCZOS)
            boxes.append(shadowed(bg, im, (x0 + (i % 2) * (cw + gap), y0 + (i // 2) * (ch + gap)), r=12))
        d = ImageDraw.Draw(bg)
        play_icon(d, x0 + PANEL_W // 2, y0 + tot_h // 2, 30)
        return boxes
    if kind == "feature":  # 大きい 1 本 + 小さい 3 本
        gap = 10
        big = load(spec["srcs"][0]).resize((PANEL_W, int(PANEL_W * 9 / 16)), Image.LANCZOS)
        sw = (PANEL_W - gap * 2) // 3
        sh_ = int(sw * 9 / 16)
        tot = big.height + gap + sh_
        y0 = PANEL_Y + (PANEL_H - tot) // 2
        boxes = [shadowed(bg, big, (PANEL_X, y0), r=14)]
        for i, n in enumerate(spec["srcs"][1:4]):
            boxes.append(
                shadowed(
                    bg,
                    load(n).resize((sw, sh_), Image.LANCZOS),
                    (PANEL_X + i * (sw + gap), y0 + big.height + gap),
                    r=10,
                )
            )
        d = ImageDraw.Draw(bg)
        play_icon(d, PANEL_X + PANEL_W // 2, y0 + big.height // 2, 34)
        rx, ry = PANEL_X + 16, y0 + 16
        d.rounded_rectangle((rx, ry, rx + 96, ry + 34), radius=8, fill="#e5483b")
        d.ellipse((rx + 12, ry + 11, rx + 24, ry + 23), fill="#fff")
        d.text((rx + 32, ry + 4), "REC", font=F(7, 22), fill="#fff")
        return boxes
    if kind == "stack":  # 「カレンダーに追加」の帯（ボタンを丸で強調）+ フッター
        ban = load("cal_banner.png")
        foot = load("footer.png")
        ban_s = contain(ban, PANEL_W, 400)
        fo_s = contain(foot, PANEL_W, 400)
        gap = 26
        tot = ban_s.height + gap + fo_s.height
        y0 = PANEL_Y + (PANEL_H - tot) // 2
        b1 = shadowed(bg, ban_s, (PANEL_X, y0), r=10)
        b2 = shadowed(bg, fo_s, (PANEL_X, y0 + ban_s.height + gap), r=10)
        btn = META["cal_banner_button"]
        k = ban_s.width / (ban.width / 2)
        bx, by = PANEL_X + btn["x"] * k, y0 + btn["y"] * k
        d = ImageDraw.Draw(bg)
        d.rounded_rectangle(
            (bx - 8, by - 8, bx + btn["width"] * k + 8, by + btn["height"] * k + 8),
            radius=14,
            outline=spec["acc"],
            width=5,
        )
        return [b1, b2]
    if kind == "clock":
        d = ImageDraw.Draw(bg)
        cx, cy, r = PANEL_X + PANEL_W // 2, PANEL_Y + PANEL_H // 2 - 10, 205
        d.ellipse((cx - r - 14 + 6, cy - r - 14 + 12, cx + r + 14 + 6, cy + r + 14 + 12), fill="#0e1430")
        d.ellipse((cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14), fill=spec["acc"])
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#fbfaf5")
        for i in range(12):
            a = math.radians(i * 30 - 90)
            L = 26 if i % 3 == 0 else 14
            d.line(
                (
                    cx + (r - 18) * math.cos(a),
                    cy + (r - 18) * math.sin(a),
                    cx + (r - 18 - L) * math.cos(a),
                    cy + (r - 18 - L) * math.sin(a),
                ),
                fill="#1c2340",
                width=8 if i % 3 == 0 else 4,
            )
        # 0 時から 4 時までの扇（切替を遅らせた幅）
        d.pieslice((cx - r + 40, cy - r + 40, cx + r - 40, cy + r - 40), -90, 30, fill=spec["acc"] + "55")

        def hand(deg, L, w):
            a = math.radians(deg - 90)
            d.line((cx, cy, cx + L * math.cos(a), cy + L * math.sin(a)), fill="#1c2340", width=w)

        hand(120, r * 0.52, 16)
        hand(0, r * 0.78, 10)
        d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill="#1c2340")
        d.text((cx - 62, cy + r * 0.3), "4:00", font=F(8, 52), fill="#1c2340")
        return [(cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14)]
    raise ValueError(kind)


SPECS = [
    dict(
        slug="2026-10-31-misskey-account",
        cat="アップデート",
        head=["Misskey", "はじめました"],
        bg="#eef4f8",
        band="#d9e9f2",
        acc="#86b300",
        ink="#1d2b36",
        kind="single",
        src="misskey.png",
        focus="Bot が投稿した告知ノート（本文・タグ・画像・リンクのカード）",
    ),
    dict(
        slug="2026-10-11-auto-recording-beta",
        cat="アップデート",
        head=["発表の撮影と", "公開を始めます"],
        bg="#fff4f2",
        band="#ffe0da",
        acc="#e5483b",
        ink="#2a1715",
        kind="feature",
        srcs=["rec_1.jpg", "rec_0.jpg", "rec_2.jpg", "rec_3.jpg"],
        focus="Hub が撮影して公開した実際の発表動画",
    ),
    dict(
        slug="2026-09-17-presentation-list-redesign",
        cat="アップデート",
        head=["発表一覧を", "ブログ形式に"],
        bg="#f2f6ff",
        band="#dde7ff",
        acc="#1b7de6",
        ink="#13233d",
        kind="single",
        src="history_cards.png",
        url="vrc-ta-hub.com/event/detail/history/",
        focus="サムネイル付きの発表カード 3 枚",
    ),
    dict(
        slug="change-day-cutoff-to-4am",
        cat="アップデート",
        head=["切替時刻を", "午前 4 時に"],
        bg="#1c2340",
        band="#283157",
        acc="#f5c542",
        ink="#ffffff",
        kind="clock",
        focus="0 時から 4 時までずらした切替時刻（時計）",
    ),
    dict(
        slug="website-management",
        cat="活動履歴",
        head=["Web サイトの", "制作・運営"],
        bg="#eefaf6",
        band="#d3f1e7",
        acc="#18a38f",
        ink="#10302a",
        kind="single",
        src="top_view.png",
        url="vrc-ta-hub.com",
        focus="Hub のトップページ",
    ),
    dict(
        slug="event-guide-book",
        cat="活動履歴",
        head=["イベント", "ガイド本"],
        bg="#fdf3e6",
        band="#f8e2c4",
        acc="#e07b00",
        ink="#3a2410",
        kind="single",
        src="book.jpg",
        focus="ガイド本の表紙",
    ),
    dict(
        slug="event-info-asset",
        cat="活動履歴",
        head=["イベント情報", "アセット"],
        bg="#f4f0ff",
        band="#e4dcff",
        acc="#7a52e0",
        ink="#211640",
        kind="single",
        src="asset.jpg",
        crop=(0, 0, 0.62, 1),
        focus="ワールドに置いたイベント情報ボード",
    ),
    dict(
        slug="fix-google-calendar-sync-bug",
        cat="アップデート",
        head=["カレンダー同期の", "不具合を修正"],
        bg="#f1f8ee",
        band="#dcefd4",
        acc="#2e9a3c",
        ink="#16301a",
        kind="single",
        src="cal_iframe.png",
        crop=(0, 0, 1, 0.66),
        focus="予定が入った Google カレンダー（上 3 週）",
    ),
    dict(
        slug="2025-01-10-ui-improvements",
        cat="アップデート",
        head=["UI の改善と", "カレンダー連携"],
        bg="#eef6fb",
        band="#d6e9f5",
        acc="#1b7de6",
        ink="#13233d",
        kind="stack",
        focus="「カレンダーに追加」ボタン（丸で強調）と新しいフッター",
    ),
    dict(
        slug="2025-07-04-vket-week-announcement",
        cat="活動履歴",
        head=["Vket 技術学術", "WEEK 開催決定"],
        bg="#fff0f7",
        band="#ffd9eb",
        acc="#e0337f",
        ink="#3a0f24",
        kind="single",
        src="vket25s.jpg",
        focus="2025 夏の告知ビジュアル（日程表入り）",
    ),
    dict(
        slug="vket-2025-summer",
        cat="活動履歴",
        head=["Vket 2025", "Summer", "動画アーカイブ"],
        bg="#fff7e8",
        band="#ffe7bd",
        acc="#f08c00",
        ink="#352208",
        kind="grid4",
        srcs=["v8_0.jpg", "v8_1.jpg", "v8_3.jpg", "v8_4.jpg"],
        focus="2025 夏の発表動画 4 本",
    ),
    dict(
        slug="vket-2025-winter",
        cat="活動履歴",
        head=["Vket 2025", "Winter", "動画アーカイブ"],
        bg="#eef3ff",
        band="#d8e2ff",
        acc="#3d63dd",
        ink="#121c3d",
        kind="grid4",
        srcs=["v9_0.jpg", "v9_1.jpg", "v9_2.jpg", "v9_4.jpg"],
        focus="2025 冬の発表動画 4 本",
    ),
    dict(
        slug="vket-2026-summer",
        cat="活動履歴",
        head=["Vket 2026", "Summer", "動画アーカイブ"],
        bg="#effaf9",
        band="#d2f1ee",
        acc="#0f9d8f",
        ink="#0d2b28",
        kind="grid4",
        srcs=["v10_0.jpg", "v10_1.jpg", "v10_3.jpg", "v10_4.jpg"],
        focus="2026 夏の発表動画 4 本",
    ),
    dict(
        slug="vrchat-nyumon-2025",
        cat="活動履歴",
        head=["VRChat 入門号を", "無償公開"],
        bg="#fff6f0",
        band="#ffe3d1",
        acc="#e8590c",
        ink="#33180a",
        kind="single",
        src="nyumon_cover-01.png",
        focus="同人誌の表紙",
    ),
]


def main():
    global S, META
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="素材と capture_meta.json のディレクトリ")
    parser.add_argument("--out", type=Path, required=True, help="<slug>.png と check.json の出力先")
    args = parser.parse_args()
    S = args.src
    OUT = args.out
    OUT.mkdir(parents=True, exist_ok=True)
    with (S / "capture_meta.json").open() as source:
        META = json.load(source)

    checks = {}
    for sp in SPECS:
        bg = Image.new("RGBA", (W, H), sp["bg"])
        d = ImageDraw.Draw(bg)
        d.polygon([(0, 470), (W, 300), (W, H), (0, H)], fill=sp["band"])
        boxes = panel(bg, sp)
        d = ImageDraw.Draw(bg)
        sf = F(6, 24)
        site = "技術・学術系イベントHub"
        d.text((TEXT_X, 58), site, font=sf, fill=sp["acc"])
        bx = TEXT_X + d.textlength(site, font=sf) + 16
        cf = F(6, 20)
        cw = d.textlength(sp["cat"], font=cf) + 28
        d.rounded_rectangle((bx, 56, bx + cw, 90), radius=17, fill=sp["acc"])
        d.text((bx + 14, 61), sp["cat"], font=cf, fill="#1c2340" if sp["acc"] == "#f5c542" else "#ffffff")
        hs = min(88, int(min(TEXT_W / d.textlength(h, font=F(8, 100)) * 100 for h in sp["head"])))
        lh = int(hs * 1.28)
        y = 110 + max(0, (H - 110 - 40 - lh * len(sp["head"])) // 2)
        text_box = [TEXT_X, y, TEXT_X, y + lh * len(sp["head"])]
        for h in sp["head"]:
            d.text((TEXT_X, y), h, font=F(8, hs), fill=sp["ink"])
            text_box[2] = max(text_box[2], TEXT_X + d.textlength(h, font=F(8, hs)))
            y += lh
        bg.convert("RGB").save(f"{OUT}/{sp['slug']}.png", optimize=True)
        # 機械チェック
        area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        overlap = any(
            not (text_box[2] + 12 <= b[0] or text_box[0] >= b[2] or text_box[3] <= b[1] or text_box[1] >= b[3])
            for b in boxes
        )
        inside = all(b[0] >= 0 and b[1] >= 0 and b[2] <= W and b[3] <= H for b in boxes)
        checks[sp["slug"]] = dict(
            headline_px=hs,
            headline_ok=hs >= 56,
            no_overlap=not overlap,
            panel_inside=inside,
            panel_ratio=round(area / (PANEL_W * PANEL_H), 2),
            panel_ok=area / (PANEL_W * PANEL_H) >= 0.3,
            focus=sp["focus"],
        )
    json.dump(checks, open(f"{OUT}/check.json", "w"), ensure_ascii=False, indent=1)
    failed = 0
    for k, v in checks.items():
        ok = all(v[x] for x in ("headline_ok", "no_overlap", "panel_inside", "panel_ok"))
        failed += not ok
        print("OK " if ok else "NG ", k, v["headline_px"], v["panel_ratio"])
    # 点検に落ちた画像があれば失敗として返す（自動実行で見落とさないため）
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

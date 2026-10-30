"""サイト画面を要素単位で撮る。注目させたい要素の位置（撮った画像内の px）も JSON に残す。"""
import argparse
import json
from pathlib import Path
def union(pg, sel, n):
    return pg.evaluate('''([sel,n]) => { const es=[...document.querySelectorAll(sel)].slice(0,n); const r=es.map(e=>e.getBoundingClientRect());
      const x=Math.min(...r.map(b=>b.left)), y=Math.min(...r.map(b=>b.top))+scrollY, x2=Math.max(...r.map(b=>b.right)), y2=Math.max(...r.map(b=>b.bottom))+scrollY;
      return {x, y, width:x2-x, height:y2-y}; }''', [sel, n])
def box(pg, sel):
    return pg.evaluate('(sel) => { const b=document.querySelector(sel).getBoundingClientRect(); return {x:b.left, y:b.top+scrollY, width:b.width, height:b.height}; }', sel)
def pad(c, p): return {'x': max(0, c['x']-p), 'y': max(0, c['y']-p), 'width': c['width']+2*p, 'height': c['height']+2*p}
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='画面と capture_meta.json の出力先')
    args = parser.parse_args()
    OUT = args.out
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale='ja-JP', timezone_id='Asia/Tokyo', device_scale_factor=2)
        # 発表一覧: 上から 3 枚のカード。幅を狭くして文字を大きく見せる
        pg = ctx.new_page(); pg.set_viewport_size({'width': 820, 'height': 1000})
        pg.goto('https://vrc-ta-hub.com/event/detail/history/', wait_until='networkidle')
        c = pad(union(pg, 'article.card', 3), 12); pg.screenshot(path=f'{OUT}/history_cards.png', clip=c, full_page=True)
        # トップの hero
        pg = ctx.new_page(); pg.set_viewport_size({'width': 1100, 'height': 900})
        pg.goto('https://vrc-ta-hub.com/', wait_until='networkidle')
        pg.screenshot(path=f'{OUT}/top_view.png')
        pg.screenshot(path=f'{OUT}/top_hero.png', clip=box(pg, 'section.top-hero'), full_page=True)
        # イベントカレンダー: 「カレンダーに追加」の帯（ボタンの位置も記録）と、予定の入った Google カレンダー
        pg = ctx.new_page(); pg.set_viewport_size({'width': 1000, 'height': 1000})
        pg.goto('https://vrc-ta-hub.com/event/list/', wait_until='networkidle')
        al = box(pg, '.alert.alert-info'); pg.screenshot(path=f'{OUT}/cal_banner.png', clip=al, full_page=True)
        btn = pg.evaluate('''() => { const a=document.querySelector('.alert.alert-info'); const t=[...a.querySelectorAll('a,button')].find(e=>/カレンダーに追加/.test(e.textContent)); const b=t.getBoundingClientRect(), r=a.getBoundingClientRect(); return {x:b.left-r.left, y:b.top-r.top, width:b.width, height:b.height}; }''')
        meta['cal_banner_button'] = btn
        pg.wait_for_timeout(9000)
        pg.screenshot(path=f'{OUT}/cal_iframe.png', clip=box(pg, 'iframe'), full_page=True)
        pg.screenshot(path=f'{OUT}/footer.png', clip=box(pg, 'footer'), full_page=True)
        b.close()
    json.dump(meta, open(f'{OUT}/capture_meta.json', 'w'))
    print(meta)


if __name__ == '__main__':
    main()

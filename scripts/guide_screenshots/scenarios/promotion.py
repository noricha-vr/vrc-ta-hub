"""Hubの広報素材と設置の操作図。外部アプリを変更せず撮影する。"""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from capture import scenario
from scenarios.settings import verify_guide

# 担当外の恒久ファイルは増やさず、モックは mocks/ 内へ一時生成する。
# 表記の根拠: 配布元の README / VCC 公式 Community Repositories。
MOCK_HTML = '''<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>設置の操作図</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#e8edf3;color:#243041;font:16px/1.7 sans-serif}
.diagram{position:relative;width:min(920px,100%);margin:auto;padding:60px 30px 30px;min-height:300px}
.badge{position:absolute;right:20px;top:12px;font-size:13px;color:#526174}.stage{display:none}.stage.active{display:block}
h1{font-size:24px;margin:0 0 20px}h2{font-size:19px}button,input,select{font:inherit;padding:10px 16px;border:1px solid #70839a;border-radius:5px;max-width:100%}
button{background:#1266b0;color:white}input,select{background:white;color:#243041}.panel{background:white;padding:22px;border:1px solid #c7d1df;border-radius:7px;margin:18px 0}
.row{display:flex;gap:20px;align-items:center;flex-wrap:wrap}.col{flex:1;min-width:180px}.tile{padding:18px;border:1px solid #9aa8bb;background:#fff;border-radius:5px}
.gallery{display:flex;gap:22px;align-items:start}.gallery a{flex:1}.gallery img{display:block;width:100%}label{display:block;margin:10px 0 4px}
.dark{background:#30343b;color:#f2f4f7;padding:20px;border-radius:5px}.dark .tile{background:#414853;color:white}.arrow{text-align:center;font-size:27px;color:#1266b0}
.board{background:#232b35;color:white;padding:22px;border:12px solid #755d3f;border-radius:6px}.board h2{font-size:23px;margin:0 0 15px}.board td{padding:12px;border-bottom:1px solid #8999a9}.board table{width:100%}
.texture{width:130px;max-height:180px;object-fit:contain}.scene{background:#dde7e8;min-height:220px;padding:25px;display:flex;justify-content:center;align-items:center}.quad{width:135px}
small{color:#596a80}.menu{padding:12px;background:#fff;border-bottom:1px solid #bbc7d5}
@media(max-width:600px){.diagram{padding:60px 22px 25px}h1{font-size:22px}.gallery{gap:12px}.panel{padding:18px}.col{min-width:100%}.board{padding:12px;border-width:7px}.board td{padding:8px;font-size:14px}}
</style><body><div class="diagram"><span class="badge">イメージ図</span>
<section class="stage" data-stage="poster-gallery"><h1>Hubのポスターを選ぶ</h1><div class="gallery">
<a id="poster-one"><img src="__POSTER1__" alt="ポスター1"></a><a id="poster-two"><img src="__POSTER2__" alt="ポスター2"></a><a id="poster-three"><img src="__POSTER3__" alt="ポスター3"></a></div><p>ポスター1　／　ポスター2　／　ポスター3</p></section>
<section class="stage" data-stage="poster-save"><h1>ブラウザで画像を保存する</h1><div class="row"><img class="texture" src="__POSTER1__" alt="ポスター1"><div class="panel"><div class="menu">画像を新しいタブで開く</div><button id="save-image">名前を付けて画像を保存...</button><div class="menu">画像をコピー</div></div></div></section>
<section class="stage" data-stage="poster-import"><h1>Unity：画像を取り込む</h1><div id="image-file" class="tile">保存した画像<br>vrc-ta-hub-poster-1.jpg</div><div class="arrow">↓ ドラッグ</div><div id="project-assets" class="dark"><strong>Project</strong><p>Assets</p><div class="tile">ここに画像を入れます</div></div></section>
<section class="stage" data-stage="poster-material"><h1>Unity：マテリアルを作る</h1><div class="panel"><strong>Project / Assets の右クリックメニュー</strong><div class="menu">Create　▸</div><button id="create-material">Material</button></div></section>
<section class="stage" data-stage="poster-texture"><h1>Unity：ポスターの見た目を設定する</h1><div class="panel"><strong>Inspector / HubPoster</strong><label>Shader</label><select id="shader"><option>Unlit/Texture</option></select><label>Base (RGB)</label><div class="row"><img id="texture-slot" class="texture" src="__POSTER1__" alt="Base (RGB) のテクスチャ欄"><p>Project の画像を<br>ここへドラッグ</p></div></div></section>
<section class="stage" data-stage="poster-quad"><h1>Unity：掲示面を置く</h1><div class="panel"><strong>GameObject → 3D Object</strong><p><button id="create-quad">Quad</button></p></div><div id="poster-surface" class="scene"><img class="quad" src="__POSTER1__" alt="Quadに適用したポスター"></div><p>Project の HubPoster を Quad へドラッグします。</p></section>
<section class="stage" data-stage="poster-transform"><h1>Unity：大きさと位置を調整する</h1><div class="panel"><strong>Inspector / Quad / Transform</strong><label>Position</label><div class="tile">壁の手前へ移動</div><label>Rotation</label><div class="tile">人が読む向きへ回転</div><label>Scale</label><div id="poster-scale" class="tile">X: 0.75　Y: 1　Z: 1</div></div></section>
<section class="stage" data-stage="poster-test"><h1>Unity：ワールドで試す</h1><div class="panel"><p>VRChat SDK → Show Control Panel → Builder</p><button id="poster-build-test">Build &amp; Test</button></div><div id="poster-check" class="scene"><img class="quad" src="__POSTER1__" alt="ワールドに掲示したポスター"></div><p>文字・向き・QRコードが読めるか確認します。</p></section>

<section class="stage" data-stage="asset-listing"><h1>Azukimochi-world</h1><div class="panel"><h2>TaAGatheringListSystem</h2><p>技術・学術系イベント情報アセット</p><button id="add-to-vcc">Add to VCC</button></div></section>
<section class="stage" data-stage="asset-repository"><h1>VCC：リポジトリを確認する</h1><div class="panel"><h2>Add Repository</h2><p>Azukimochi-world</p><p>TaAGatheringListSystem</p><button id="confirm-repository">I Understand, Add Repository</button></div></section>
<section class="stage" data-stage="asset-project"><h1>VCC：Projects</h1><div class="panel"><h2>さくらの広場</h2><p>Worlds</p><button id="manage-project">Manage Project</button></div></section>
<section class="stage" data-stage="asset-package"><h1>VCC：Manage Project</h1><div class="panel"><h2>Available Packages</h2><div class="row"><span>TaAGatheringListSystem</span><button id="add-package" aria-label="パッケージを追加">＋</button></div></div></section>
<section class="stage" data-stage="asset-open"><h1>VCC：Manage Project</h1><div class="panel"><h2>さくらの広場</h2><p>Installed Packages：TaAGatheringListSystem</p><button id="open-project">Open Project</button></div></section>
<section class="stage" data-stage="asset-prefab"><h1>Unity：表示板を配置する</h1><div class="dark"><strong>Project / Packages / TaAGatheringListSystem</strong><div id="prefab" class="tile">TaAGatheringInfoSys.prefab</div></div><div class="arrow">↓ ドラッグ</div><div id="hierarchy" class="dark"><strong>Hierarchy</strong><div class="tile">さくらの広場<br>　TaAGatheringInfoSys</div></div></section>
<section class="stage" data-stage="asset-transform"><h1>Unity：表示板を調整する</h1><div class="panel"><strong>Inspector / TaAGatheringInfoSys</strong><div id="asset-transform" class="tile"><h2>Transform</h2><p>Position：置く位置</p><p>Rotation：向き</p><p>Scale：大きさ</p></div></div><p>来た人が近づいて読める高さに置きます。</p></section>
<section class="stage" data-stage="asset-test"><h1>Unity：ワールドで試す</h1><div class="panel"><p>VRChat SDK → Show Control Panel → Builder</p><button id="asset-build-test">Build &amp; Test</button></div></section>
<section class="stage" data-stage="asset-result"><h1>VRChat：表示を確認する</h1><div class="board"><h2>技術・学術系集会情報</h2><table id="event-list"><tr><td>月曜日</td><td>22:00 さくら技術交流会<br>ワールド制作の発表</td></tr><tr><td>火曜日</td><td>22:00 ひなた科学の会<br>身近な科学を楽しむ</td></tr></table><p>説明用の架空データ</p></div></section>
</div><script>function showStage(stage){document.querySelectorAll('.stage').forEach(e=>e.classList.toggle('active',e.dataset.stage===stage))}</script></body></html>'''


@contextmanager
def promotion_mock():
    """外部画面のHTMLは撮影中だけ mocks/ 配下に置き、終了時に片付ける。"""
    root = Path(__file__).resolve().parents[1]
    html = MOCK_HTML
    posters = root.parents[1] / 'app/guide/static/guide/images/posters'
    for index in range(1, 4):
        html = html.replace(f'__POSTER{index}__', (posters / f'vrc-ta-hub-poster-{index}.jpg').as_uri())
    with TemporaryDirectory(prefix='promotion-demo-', dir=root / 'mocks') as tmp:
        file = Path(tmp) / 'promotion.html'
        file.write_text(html)
        yield file.relative_to(root / 'mocks').as_posix()


def diagram(capture, file, stage, *targets):
    capture.mock(file)
    capture.page.evaluate('(stage) => showStage(stage)', stage)
    for number, target in enumerate(targets, 1):
        capture.annotate(target, number)
    capture.shot(selector='.diagram')


@scenario('promotion/poster', mobile=True)
def poster(capture):
    with promotion_mock() as file:
        for stage, targets in [
            ('poster-gallery', ('#poster-one', '#poster-two', '#poster-three')),
            ('poster-save', ('#save-image',)),
            ('poster-import', ('#image-file', '#project-assets')),
            ('poster-material', ('#create-material',)),
            ('poster-texture', ('#shader', '#texture-slot')),
            ('poster-quad', ('#create-quad', '#poster-surface')),
            ('poster-transform', ('#poster-scale',)),
            ('poster-test', ('#poster-build-test', '#poster-check')),
        ]:
            diagram(capture, file, stage, *targets)
    response = capture.page.request.get(capture.base_url + '/community/1/poster/download/')
    assert response.ok and response.headers['content-type'].startswith('image/')
    capture.page.route('**/media/poster/1/**', lambda route: route.fulfill(response=response))
    capture.goto('/community/1/')
    target = 'a:has-text("集会を紹介するためのポスターの転載可")'
    capture.annotate(target, 1)
    capture.shot(selector=f'{target} >> xpath=..')
    verify_guide(capture)


@scenario('promotion/asset', mobile=True)
def asset(capture):
    with promotion_mock() as file:
        for stage, targets in [
            ('asset-listing', ('#add-to-vcc',)),
            ('asset-repository', ('#confirm-repository',)),
            ('asset-project', ('#manage-project',)),
            ('asset-package', ('#add-package',)),
            ('asset-open', ('#open-project',)),
            ('asset-prefab', ('#prefab', '#hierarchy')),
            ('asset-transform', ('#asset-transform',)),
            ('asset-test', ('#asset-build-test',)),
            ('asset-result', ('#event-list',)),
        ]:
            diagram(capture, file, stage, *targets)
    verify_guide(capture)

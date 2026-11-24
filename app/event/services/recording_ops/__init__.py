"""録画運用（プロキシ配信・EDL v1・確定指示キュー・補償停止）パッケージ。

3 層構造で責務を分離し、公開シンボルは各サブモジュールから明示的に import する。
- Layer 1（純粋ロジック・DB 非依存）: ``errors``, ``edl``
- Layer 2（基盤・状態機械）: ``storage``, ``locks``, ``stop_queue``
- Layer 3（ユースケース）: ``items``, ``orders``, ``worker``, ``completion``, ``revocation``
"""

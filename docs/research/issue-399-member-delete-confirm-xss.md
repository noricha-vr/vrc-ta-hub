# Issue 399: スタッフ削除確認の表示名エスケープ

## 調査対象

- `app/community/templates/community/member_manage.html` のスタッフ削除フォームは、`display_label` を `confirm('...')` の JavaScript 文字列リテラルへ埋め込んでいた。
- `app/user_account/models.py` の `CustomUser.display_name` は `blank=True` の任意表示名で、`display_label` は `display_name` があれば優先し、未設定時だけ `user_name` にフォールバックする。
- `app/user_account/forms.py` のプロフィール編集フォームは `display_name` に専用の文字種制限を設けていない。
- 同じ `display_label` と `confirm` の組み合わせをテンプレート群で検索したところ、未エスケープの該当箇所はスタッフ削除確認だけだった。Vket の発表削除確認は既に `escapejs` を使っている。

## 原因候補と改善案

`display_name` は引用符、改行、バックスラッシュなどを含められる人間向けプロフィール項目であるため、HTML表示では問題なくても JavaScript 文字列コンテキストでは別途エスケープが必要になる。

改善案は、テンプレート上で `escapejs` を適用する案と、表示名を `data-*` 属性へ移して外部 JavaScript で確認文を組み立てる案を比較した。

## 採用判断

既存テンプレートでは同種の削除確認に `escapejs` を使う実装があり、今回の問題は単一の JavaScript 文字列コンテキストに限定されているため、`member.user.display_label|escapejs` を採用した。

この変更により、表示名 `');alert(1);//` は `\u0027)\u003Balert(1)\u003B//` のような文字列として描画され、`confirm` の文字列リテラルを閉じられない。

## 検証手順

- スタッフ管理ページのレスポンスに、攻撃例の表示名が画面表示用テキストとして HTML エスケープされていることを確認する。
- 同じ表示名が `onsubmit` 内では JavaScript 文字列用にエスケープされ、raw な `confirm('');alert(1);// を削除しますか？')` 形式にならないことを確認する。
- 影響範囲として `app.community` と `app.user_account` の Django テストを実行する。

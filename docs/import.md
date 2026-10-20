# ほかのタイトルから素材を取り込む

ROM A の素材を ROM B へ持ち込む手順です。単純なファイルコピーでは動きません。
3つが違うからです。

| 違うもの | 本ツールの対応 |
| --- | --- |
| **パス** — タイトルごとにディレクトリ構成が違う | 対応付けファイル（mapping）で明示。下書きは `reatk map` が自動生成 |
| **圧縮** — 元が LZ77 ならゲームは BIOS 展開を呼ぶ | 取り込み時に同じ形式で再圧縮。再現できない形式なら**書かずに理由を報告** |
| **エンコード** — タイル形状・ビット深度・共有パレット | 対象側のタイル形状とパレットに合わせて再量子化して NCGR を再生成 |

## 1. 対応付けの下書きを作る

```sh
reatk map --from donor.nds --to target.nds -o map.json
```

拡張子が一致するファイル同士を、名前の近さ（70%）とサイズの近さ（30%）で採点して
ペアにします。出力には `confidence` が入り、低いものには
`(names barely match)` が付きます。**下書きは推測なので、必ず目で直してください。**

```json
{
  "schema": 1,
  "entries": [
    { "from": "/data/hero.ncgr", "to": "/a/0/chr/player.ncgr", "mode": "auto",
      "note": "drafted, confidence 0.30 (names barely match)" }
  ],
  "unmatched": {
    "donor_files_unused": ["..."],
    "target_files_without_a_source": ["..."]
  }
}
```

`mode` は3つ。

| mode | 動作 |
| --- | --- |
| `auto` | 対象が NCGR で元が画像なら `image`、それ以外は `raw`（既定） |
| `image` | 元を RGBA に起こし、**対象側のパレット・ビット深度・タイル形状**で NCGR を再生成 |
| `raw` | バイト列をそのまま（圧縮だけ合わせる） |

## 2. 取り込む

```sh
# まず何が起きるかだけ見る（何も書かない）
reatk import --from donor.nds --to target.nds --map map.json --dry-run

# 置き換わったファイルだけ書き出す
reatk import --from donor.nds --to target.nds --map map.json -o out/

# アプリごと作る（置き換えは reatk/overrides/ に同梱され manifest に記録）
reatk import --from donor.nds --to target.nds --map map.json --emit apk -o build/
```

ドナーには **ROM でもディレクトリでも**指定できます。ディレクトリなら PNG を
直接ソースにできます（`reatk extract --render-graphics` で出した PNG を編集 →
そのまま取り込み、が一番楽です）。

```sh
reatk extract donor.nds -o A/ --all      # A/romfs/.../sheet.png が出る
# sheet.png を好きな絵に描き換える
reatk import --from A/romfs --to target.nds --map map.json -o out/
```

## パレットの扱い（重要）

絵を差し替えるとき、量子化の基準にするパレットは**実行時に存在する方**でなければ
なりません。本ツールは mapping を先読みして判断します。

* mapping に対象ディレクトリのパレット差し替えも含まれる → **ドナー側のパレット**で量子化
  （`re-encoded ... onto the donor palette this mapping also imports`）
* 含まれない → **対象の既存パレット**で量子化（`onto the target's own palette`）

後者の場合、元の16色に寄せられるので色が変わります。意図した色にしたいなら
パレットも一緒に差し替えてください。ただしそのパレットを他のシートが共有していると
そちらの色も変わります。

## 断るケース（黙って壊さない）

| 状況 | 動作 |
| --- | --- |
| 対象が LZ11 / Huffman / RLE 圧縮 | スキップして報告。`--force-raw` で非圧縮のまま強行も可（ゲーム側が読めなくなる可能性を明示） |
| サイズ（ピクセル寸法）が違う | スキップ。`32x16 なのに 8x8` のように両方の寸法を出す |
| 対象 ROM にそのパスが無い | スキップして報告 |
| ドナーの NCGR の隣にパレットが無い | スキップ。「PNG で出してから取り込め」と案内 |

`--report report.json` で機械可読な記録が残ります。

## できないこと

* **ゲームのロジック（機能）の移植**。エンジンが別物なので、ツールで移せるのは
  素材（画像・音声・テキスト・フォント）だけです。コードを移したい場合は ARM9 を
  自分で解析してください（`reatk extract --decompress` が BLZ を解いた
  `arm9.dec.bin` を出します）。
* **ファイルサイズが増える差し替えを ROM 本体に書き戻すこと**。本ツールは ROM を
  再構築しません。差し替えはアプリ側の `reatk/overrides/` 経由で読ませる設計です。
* **音声フォーマットの変換**（SWAR の中身の再エンコードなど）。SDAT は分解・一覧
  までです。

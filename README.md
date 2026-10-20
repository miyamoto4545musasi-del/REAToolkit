# REAToolkit

携帯機の ROM イメージを **解析（リバースエンジニアリング）** し、**Android / iOS の
プロジェクトへ変換** するためのキットです。GitHub にある REA 系ツール（ROM の
ヘッダ・ファイルシステム解析ツール群）の考え方を踏襲しつつ、「解析 → 変換 →
モバイルアプリ化」までを 1 つのパイプラインにまとめ、途中に**自分の機能を差し込める
プラグイン機構**を持たせています。

依存ライブラリはゼロ（Python 3.10+ の標準ライブラリのみ）。

> **重要**: 本キットは ROM / BIOS / エミュレータコアを一切含まず、取得もしません。
> 自作 homebrew か、自分が所有するカートリッジから自分用に取得したデータに対して
> 使ってください。詳しくは [LEGAL.md](LEGAL.md)。

## インストール

```sh
git clone https://github.com/miyamoto4545musasi-del/REAToolkit
cd REAToolkit
pip install -e .
```

## 使い方

```sh
# ヘッダ情報
reatk info game.nds

# 解析レポート（整合性 / エントロピー / 残留デバッグ情報 / 未使用領域）
reatk analyze game.nds
reatk analyze game.nds --json report.json

# 内蔵ファイルシステム（NitroFS）の一覧と抽出
reatk ls game.nds --glob '*.sdat'
reatk extract game.nds -o out/        # code/ banner/ romfs/ に展開
reatk extract game.nds -o out/ --all  # + BIOS 圧縮の展開 / NARC 再帰 / SDAT / PNG 化

# 単体ファイルの操作
reatk decompress data.lz -o data.bin  # LZ77 / LZ11 / Huffman / RLE
reatk narc pack.narc                  # NARC の一覧（-o で抽出）
reatk sdat sound.sdat                 # SSEQ/SBNK/SWAR の一覧（-o で抽出）
reatk gfx sheet.ncgr palette.nclr -o sheet.png
reatk decompress arm9.bin -o arm9.dec --blz   # ARM9 の BLZ 圧縮

# ほかのタイトルから素材を取り込む
reatk map --from donor.nds --to target.nds -o map.json       # 対応付けの下書き
reatk import --from donor.nds --to target.nds --map map.json --dry-run
reatk import --from donor.nds --to target.nds --map map.json --emit apk -o build/

# APK プロジェクトを生成（ROM は同梱せず、起動後に端末から読み込む）
reatk convert game.nds -t apk -o build/android

# IPA プロジェクトを生成（縦画面固定、タッチ操作レイアウト付き）
reatk convert game.nds -t ipa -o build/ios \
    --orientation portrait --plugin touch-controls

# 自分の ROM をアプリに同梱する場合は所有の明示が必要
reatk convert homebrew.nds -t apk -o build/hb --embed-rom --i-own-this-rom
```

## 構成

| レイヤ | 役割 |
| --- | --- |
| `reatk.formats` | NDS / GBA ヘッダ、NitroFS、バナー、**NARC**、**SDAT**、**NCLR/NCGR**、**BIOS 圧縮**（LZ77/LZ11/Huffman/RLE） |
| `reatk.analyze` | 読み取り専用の検査パス（CRC、エントロピー、残留文字列、未使用領域、中身の内訳） |
| `reatk.convert` | アセット変換（グラフィック ⇄ PNG、圧縮展開、アーカイブ再帰展開）。PNG の読み書きとも自前実装 |
| `reatk.transfer` | タイトル間の素材移植（対応付け・再圧縮・再エンコード） |
| `reatk.package` | Android（Gradle + GL 描画 + AAudio + JNI ホスト）/ iOS（XcodeGen + Swift + AVAudioEngine）プロジェクトの生成 |
| `reatk.plugins` | 各段階に割り込むユーザー機能 |

### 生成されるプロジェクトについて

**コアを入れなくてもビルドして起動します。** その場合はテストパターンが動き、
2画面のレイアウトとタッチ操作をその場で確認できます（描画経路の問題とコアの
不在を切り分けられる）。

生成されるもの:

* **描画** — Android は GLSurfaceView + シェーダでフレームをテクスチャ化し、
  manifest のレイアウトに従って上下画面を別々の矩形へ描画。iOS は CADisplayLink
  と CALayer（`contentsRect` で上下に分割）。
* **音声** — Android は AAudio（コアの `retro_get_system_av_info` のサンプルレート
  で開く）、iOS は AVAudioEngine の `AVAudioSourceNode`。
* **入力** — manifest のレイアウトから十字キー（斜め入力対応）・A/B/X/Y・L/R・
  START/SELECT を描画し、下画面の矩形へのタッチはスタイラス座標へ変換。
* **BIOS/セーブの置き場** — アプリ専用領域をコアの system directory として渡します。

自分で用意するもの:

* **エミュレータコア** — libretro コア（melonDS 等）。Android は
  `jniLibs/<abi>/lib<core>_libretro_android.so` に置けば起動時に自動で見つかります。
  iOS は `dlopen` できないので静的リンクし、`EmulatorCore` プロトコルを実装して
  `EmuCore.shared` に差します。
* **`libretro.h`**（libretro-common から）と**署名キー**。
* セーブステートや高解像度描画が必要なら、そこは手を入れてください
  （各プロジェクトの README に Known limits として明記してあります）。

対応フォーマットの詳細と未対応のもの（NSCR の画面合成など）は
[docs/formats.md](docs/formats.md)、タイトル間の素材移植は
[docs/import.md](docs/import.md) に書いてあります。

## プラグイン（オリジナル機能の追加）

4 つのフックがあります。詳細は [docs/plugins.md](docs/plugins.md)。

```python
from reatk.plugins import Plugin

class MyPlugin(Plugin):
    name = "my-plugin"
    description = "やりたいこと"

    def on_rom(self, rom, ctx): ...          # 解析直後、書き出し前
    def on_asset(self, entry, data, ctx): ...# ROM 内ファイル 1 件ごと
    def on_manifest(self, manifest, ctx): ...# アプリ manifest 生成前
    def on_project(self, project_dir, ctx): ...# ファイル書き出し後
```

同梱プラグイン:

| 名前 | 内容 |
| --- | --- |
| `ips-patch` | IPS パッチをメモリ上の ROM に適用（`--set ips=patch.ips`） |
| `asset-replace` | ROM 内ファイルを自前のファイルに差し替え（`--set assets=./mod`）。`extract` では展開結果に反映、`convert` ではアプリ内 `reatk/overrides/` に同梱して manifest に記録 |
| `cross-import` | 別タイトルから素材を取り込む（`--set from=donor.nds --set map=map.json`） |
| `touch-controls` | タッチ操作レイアウトを manifest に書き込む（`--set layout=my.json`） |

```sh
reatk plugins                                    # 一覧
reatk convert game.nds -t apk -o out \
    --plugin-dir ./my_plugins --plugin my-plugin --set key=value
```

## テスト

合成した ROM（著作物を含まない自前生成のフィクスチャ）でパイプライン全体を検証します。

```sh
python -m unittest discover -s tests -t .
```

## ライセンス

MIT（[LICENSE](LICENSE)）。生成物に組み込むコアや ROM のライセンスは別です。

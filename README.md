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
| `reatk.formats` | NDS / GBA のヘッダ、NitroFS、バナー（アイコン・多言語タイトル）の解析 |
| `reatk.analyze` | 読み取り専用の検査パス（CRC、エントロピー、残留文字列、未使用領域） |
| `reatk.convert` | アセット変換（アイコン → PNG、ROM 内ファイルの展開）。PNG 書き出しも自前実装 |
| `reatk.package` | Android（Gradle + JNI ホスト）/ iOS（XcodeGen + Swift）プロジェクトの生成 |
| `reatk.plugins` | 各段階に割り込むユーザー機能 |

### 生成されるプロジェクトについて（ここは正直に）

生成されるのは**ビルド可能なプロジェクトの骨組み**です。以下は意図的に含めていません。

* **エミュレータコア** — libretro コア（melonDS / DeSmuME など）を自分で用意し、
  Android なら `jniLibs/<abi>/`、iOS なら静的リンクで組み込みます。
* **`libretro.h`** — libretro-common から取得して `cpp/` に置きます。
* **署名** — 自分の署名で各自ビルドします。
* **映像・音声の最終出力** — Android 側は `reatk_host.c` がコアを `dlopen` して
  フレームを RGBA で JVM へ渡すところまで実装済み。GL/Metal への描画と音声出力は
  `TODO` として明示してあります。

つまり「ROM を入れたら動くアプリが出てくる」ツールではなく、**1 タイトル専用の
フロントエンドを機械的に用意するツール**です。

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
| `asset-replace` | ROM 内ファイルを自前のファイルに差し替え（`--set assets=./mod`） |
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

# プラグインの書き方

プラグインは `reatk.plugins.Plugin` のサブクラスです。ファイルを 1 つ置いて
`--plugin-dir` で指定すれば読み込まれます（`reatk.plugins.builtin` に置く方法も
同じように動きます）。

## フックの実行順

```
reatk convert game.nds -t apk -o out
  │
  ├─ ROM をパース
  ├─ on_rom(rom, ctx)                 rom.data を書き換えてもよい（IPS 適用など）
  ├─ on_asset(entry, data, ctx)       NitroFS の 1 ファイルごと。bytes を返すと差し替え
  │                                   （extract でも convert でも呼ばれる。convert では
  │                                    変わったファイルだけがアプリ内 reatk/overrides/ へ）
  ├─ on_manifest(manifest, ctx)       dict を返すとアプリ manifest を置き換え
  ├─ プロジェクトをディスクへ書き出し
  └─ on_project(project_dir, ctx)     生成物に追記・追加ファイルを置く
```

未実装のフックは呼ばれても何もしません。必要なものだけ書いてください。

## ctx（PluginContext）

| 属性 | 内容 |
| --- | --- |
| `ctx.option("key", default)` | `--set key=value` で渡された値 |
| `ctx.state` | フック間で共有される作業用 dict |
| `ctx.note("...")` | 実行サマリに 1 行出す |

複数プラグインが同じフックを実装した場合、**登録順に数珠つなぎ**で適用されます。
`on_asset` / `on_manifest` は前のプラグインの戻り値を次が受け取ります。

## 例: 起動時の設定ファイルを足す

```python
import json
from reatk.plugins import Plugin

class DefaultSettings(Plugin):
    name = "default-settings"
    description = "初期設定ファイルを生成物に追加する"

    def on_manifest(self, manifest, ctx):
        manifest["settings"] = {
            "frameskip": int(ctx.option("frameskip", 0)),
            "screen_layout": ctx.option("layout", "stacked"),
        }
        return manifest

    def on_project(self, project_dir, ctx):
        target = project_dir / "docs" / "settings.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("生成時の初期設定はこのプラグインが決めています\n", "utf-8")
        ctx.note("default-settings: docs/settings.md を追加")
```

```sh
reatk convert game.nds -t apk -o out \
    --plugin-dir ./my_plugins --plugin default-settings --set frameskip=1
```

## 同梱プラグイン

| 名前 | 使うフック | 内容 |
| --- | --- | --- |
| `ips-patch` | `on_rom` | IPS パッチを適用 |
| `asset-replace` | `on_asset` | ディレクトリの同名ファイルで差し替え |
| `cross-import` | `on_rom` + `on_asset` | 別タイトルから素材を取り込む（[docs/import.md](import.md)） |
| `touch-controls` | `on_manifest` | タッチ操作レイアウトを書き込む |

## 注意

* `on_rom` で `rom.data` を差し替えると、以降の解析・抽出・同梱すべてに反映されます。
  ヘッダ CRC は再計算されないので、整合性が必要な場合は自分で更新してください。
* `on_asset` に渡る `entry.path` は ROM 内のデータであり、信用できる入力ではありません。
  パスを使ってファイルを書く場合は必ず出力先の中に収まることを確認してください
  （`reatk.convert.extract._safe_target` が同じことをしています）。

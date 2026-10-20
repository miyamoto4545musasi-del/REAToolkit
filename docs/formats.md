# 対応フォーマット

## コンテナ

| フォーマット | 対応状況 |
| --- | --- |
| NDS / DSi カートリッジ（`.nds`, `.srl`） | ヘッダ、ARM9/ARM7、オーバーレイ、NitroFS、バナー |
| GBA カートリッジ（`.gba`） | ヘッダとチェックサムのみ（判別用） |
| NARC アーカイブ | 名前あり/なし両対応、再帰展開 |
| SDAT サウンドアーカイブ | SYMB/INFO/FAT を読み、SSEQ/SSAR/SBNK/SWAR/STRM を一覧・抽出 |

## 圧縮（BIOS 圧縮）

| 種別 | ヘッダ | 展開 | 圧縮 |
| --- | --- | --- | --- |
| LZ77 | `0x10` | ✅ | ✅（greedy 最長一致） |
| LZ11 | `0x11` | ✅ | — |
| Huffman 4bit | `0x24` | ✅ | — |
| Huffman 8bit | `0x28` | ✅ | — |
| RLE | `0x30` | ✅ | — |

`detect()` はヘッダバイトだけでなく宣言サイズの妥当性も見るため、通常データを
誤検出しにくくしてあります。誤検出した場合でも `maybe_decompress()` は元データを
そのまま返すので、データが失われることはありません。

**未対応**: ARM9 バイナリの BLZ（backwards LZ）。これは BIOS ではなくリンカが
かけるもので別形式です。`reatk analyze` は ARM9 のエントロピーを出すので、
7.5 を超えていれば圧縮・暗号化されていると判断できます。

## グラフィック

| フォーマット | 対応状況 |
| --- | --- |
| NCLR（パレット） | ✅ BGR555 → RGBA、16色/256色、スロット指定可 |
| NCGR（キャラクタ） | ✅ 4bpp/8bpp のデタイル、幅宣言なしの場合は 8 タイル幅で推定 |
| NSCR（スクリーン） | — 判別のみ。タイル配置の合成は未実装 |
| NCER（セル） | — 判別のみ |

NCGR は**タイルの並びそのまま**が PNG になります。画面として正しく組み上げるには
NSCR が必要なので、スプライトシートやタイルセットの確認用と考えてください。

```sh
reatk gfx sheet.ncgr palette.nclr -o sheet.png --palette 2
reatk extract game.nds -o out --render-graphics   # 同じディレクトリの .nclr と自動ペア
```

## 使えるコマンド

```sh
reatk detect *.nds *.gba          # 判別
reatk info / analyze / ls / strings
reatk extract game.nds -o out --all      # 展開・NARC 再帰・SDAT・PNG 化すべて
reatk decompress file.lz -o file.bin     # 単体の展開
reatk decompress file.bin -o file.lz --compress
reatk narc pack.narc              # 一覧（-o で抽出）
reatk sdat sound.sdat             # 一覧（-o で抽出）
reatk convert game.nds -t apk -o build/android
```

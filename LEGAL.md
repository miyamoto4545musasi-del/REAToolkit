# Legal notes / 法的な注意

REAToolkit is an analysis and project-generation tool. Read this before using
it on anything you did not write yourself.

## What this toolkit does not do

* It does **not** contain, download or generate any ROM image, BIOS, firmware
  or emulation core. Every generated project expects you to supply those.
* It does **not** circumvent copy protection. It parses images that have
  already been dumped.
* It does **not** produce a signed, installable app. You build and sign the
  generated project with your own identity.

## What is on you

* **Dumps.** Dump cartridges you own, for your own use. Whether that is
  permitted depends on where you live — in Japan, private copying of a work
  you own is narrow and circumventing technical protection is not covered by
  it (著作権法 第30条, 第120条の2); in the US, see 17 U.S.C. §117 and §1201.
  Downloading a ROM you do not own is infringement essentially everywhere.
* **Embedding.** `--embed-rom` copies the image into the app bundle. That is a
  reproduction, and distributing the result is redistribution. The flag
  therefore requires `--i-own-this-rom`, and the default behaviour is to let
  the app import a ROM from the device at runtime instead.
* **Cores and BIOS.** libretro cores have their own licences (melonDS and
  DeSmuME are GPL, which has consequences for an app you distribute). DS BIOS
  and firmware files are Nintendo's; dump them from your own console.
* **Trademarks.** Game titles, logos and icons are trademarks of their owners.
  The toolkit converts a title's icon for your own build; that is not a licence
  to publish it.
* **Store distribution.** Apple and Google both reject emulator apps bundling
  copyrighted content. Treat the output as a personal sideload.

## 日本語の要約

本ツールキットは解析とプロジェクト生成のみを行い、ROM・BIOS・エミュレータ
コアは一切含みません。自分で作った homebrew、または自分が所有する
カートリッジから自分用に取得したデータに対して使ってください。`--embed-rom`
は ROM をアプリへ複製する操作なので `--i-own-this-rom` の明示が必要です。
生成物の配布（APK/IPA の再配布、ROM・BIOS の同梱）は行わないでください。

The authors provide this tool as-is and take no responsibility for how it is
used.

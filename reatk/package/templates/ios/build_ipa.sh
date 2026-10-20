#!/bin/sh
# Build an unsigned .ipa for sideloading with your own tooling.
#
# Requires: xcodegen, Xcode command line tools, and your own signing identity.
set -eu

cd "$(dirname "$0")/.."
xcodegen generate

xcodebuild \
  -project "@@TARGET@@.xcodeproj" \
  -scheme "@@TARGET@@" \
  -configuration Release \
  -sdk iphoneos \
  -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO \
  build

rm -rf build/ipa && mkdir -p build/ipa/Payload
cp -R "build/Build/Products/Release-iphoneos/@@TARGET@@.app" build/ipa/Payload/
cd build/ipa && zip -qry "../@@TARGET@@.ipa" Payload
echo "wrote build/@@TARGET@@.ipa (unsigned - sign it before installing)"

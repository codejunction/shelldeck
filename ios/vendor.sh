#!/bin/sh
# SwiftTerm's Package.swift drops its iOS sources whenever the manifest runs on Linux, and xtool always
# builds on Linux, so SwiftTerm is fetched here and compiled as a local target. Run before `xtool dev`.
set -e
cd "$(dirname "$0")"
V=v1.19.0
DEST=Vendor/SwiftTerm
[ "$(cat "$DEST/.version" 2>/dev/null)" = "$V" ] && exit 0
TMP=$(mktemp -d)
git clone -q --depth 1 --branch "$V" https://github.com/migueldeicaza/SwiftTerm.git "$TMP"
# a tag can be moved; the commit can't
[ "$(git -C "$TMP" rev-parse HEAD)" = 464df5207fc2432e16c9a23abe538187196daf5f ] || { echo "SwiftTerm $V is not the reviewed commit" >&2; exit 1; }
rm -rf "$DEST" && mkdir -p Vendor
cp -r "$TMP/Sources/SwiftTerm" "$DEST"
cp "$TMP/LICENSE" "$DEST/LICENSE"
# docs, and the Metal shader (no Metal compiler on Linux; the default CoreGraphics renderer is used).
# Mac/ stays: iOS uses its AccessibilityService.
rm -rf "$DEST/Documentation.docc" "$DEST/Apple/Metal/Shaders.metal"
# stands in for SwiftTerm's build-info plugin
cat > "$DEST/BuildInfo.swift" <<EOF
public enum SwiftTermBuildInfo {
    public static let branch: String? = nil
    public static let tag: String? = "$V"
    public static let commit: String? = nil
    public static let hasUncommittedChanges: Bool? = nil
    public static let version: String = "${V#v}"
}
EOF
echo "$V" > "$DEST/.version"
rm -rf "$TMP"

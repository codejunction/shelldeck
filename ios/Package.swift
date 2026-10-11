// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "Shelldeck",
    platforms: [.iOS(.v17), .macOS(.v14)],
    // xtool: exactly one library product, the app
    products: [.library(name: "Shelldeck", targets: ["Shelldeck"])],
    targets: [
        // ponytail: Swift 5 mode, since SwiftTerm's delegate and URLSession callbacks aren't Sendable-annotated
        .target(name: "Shelldeck", dependencies: ["SwiftTerm"], swiftSettings: [.swiftLanguageMode(.v5)]),
        // fetched by vendor.sh (see there for why it isn't a package dependency)
        .target(name: "SwiftTerm", path: "Vendor/SwiftTerm", exclude: ["LICENSE"],
                swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)

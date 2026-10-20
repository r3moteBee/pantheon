# iOS app (Xcode-ready)

Build an iPhone/iPad app as ONE artifact folder the user can download, open in Xcode, build and push to GitHub. Every file goes through `save_to_artifact` (never `write_file`, which only makes workspace scratch the user can't download).

## Layout

Use the app name in UpperCamelCase (`<App>` below, e.g. `MarbleMaze`). All paths are inside the artifact folder `<App>/`:

```
<App>/README.md                         how to open and run it (template below)
<App>/project.yml                       XcodeGen spec (template below); Xcode generates the project from it
<App>/<App>Core/Package.swift           the app's logic as a Swift package
<App>/<App>Core/Sources/<App>Core/*.swift
<App>/<App>Core/Tests/<App>CoreTests/*.swift
<App>/App/<App>App.swift                @main App
<App>/App/*.swift                       SwiftUI views, device glue (CoreMotion, UIKit, …)
```

Do NOT write a `.xcodeproj` / `project.pbxproj` by hand: it is a generated format and hand-written ones fail to open.

## Rules for the code

- **`<App>Core` is plain Swift: `import Foundation` only.** No SwiftUI, UIKit, CoreMotion, SpriteKit or Combine in it. Game rules, physics, models, scoring and state live here as value types and functions that take their inputs as parameters (e.g. a physics `step(state, tilt: Vector, dt:)` takes the tilt as a value; the app reads the sensor and passes it in). Mark what the app uses `public`.
- **Tests** (XCTest) cover the logic: the rules, edge cases and every state change the app relies on. Use `@testable import <App>Core`.
- **`App/`** holds the SwiftUI views and device access, and `import <App>Core`. Keep sensor code (e.g. `CMMotionManager`) in one small class that publishes plain values to the views.
- Support iPhone and iPad, portrait and landscape, with layouts that use `GeometryReader` or flexible stacks rather than fixed point sizes.

## Steps

1. **Plan** the files (one line each) in the layout above.
2. **Save the core package** with `save_to_artifact`: `Package.swift`, the sources, the tests.
3. **Check it.** If the `run_build` tool is available, call `run_build(path="<App>/<App>Core/", toolchain="swift")`. Fix every error and failing test with `update_artifact` (pass the complete file) and run it again until it PASSES. If `run_build` is not available, re-read the files and check them carefully instead.
4. **Save the app files** in `App/`.
5. **Save `project.yml` and `README.md`** from the templates below, with `<App>` filled in.
6. **Reply** with the file list, what the tests cover, the `run_build` result, and the download link `run_build` gave you (or tell the user to download the `<App>` folder from the Artifacts page).

## `Package.swift`

```swift
// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "<App>Core",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "<App>Core", targets: ["<App>Core"])],
    targets: [
        .target(name: "<App>Core"),
        .testTarget(name: "<App>CoreTests", dependencies: ["<App>Core"]),
    ]
)
```

## `project.yml`

```yaml
name: <App>
options:
  bundleIdPrefix: com.example
  deploymentTarget:
    iOS: "17.0"
packages:
  <App>Core:
    path: <App>Core
settings:
  base:
    SWIFT_VERSION: "5.0"
    TARGETED_DEVICE_FAMILY: "1,2"
    GENERATE_INFOPLIST_FILE: YES
    INFOPLIST_KEY_UILaunchScreen_Generation: YES
    INFOPLIST_KEY_UIApplicationSceneManifest_Generation: YES
    INFOPLIST_KEY_UISupportedInterfaceOrientations: "UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight"
    INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad: "UIInterfaceOrientationPortrait UIInterfaceOrientationPortraitUpsideDown UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight"
    MARKETING_VERSION: "1.0"
    CURRENT_PROJECT_VERSION: "1"
targets:
  <App>:
    type: application
    platform: iOS
    sources: [App]
    dependencies:
      - package: <App>Core
```

If the app needs a privacy permission (camera, location, motion *activity* - plain accelerometer/gyro data needs none), add its `INFOPLIST_KEY_NS…UsageDescription` line under `settings.base`.

## `README.md`

```markdown
# <App>

<one paragraph: what the app does>

## Open in Xcode

With [XcodeGen](https://github.com/yonaskolb/XcodeGen) (recommended):

    brew install xcodegen
    cd <App>
    xcodegen generate
    open <App>.xcodeproj

Then pick an iPhone/iPad simulator (or your device - set your team under Signing & Capabilities) and press Run.

Without XcodeGen: in Xcode create a new iOS App project named <App> (SwiftUI), delete its template ContentView.swift and <App>App.swift, drag the `App` folder into the project, then File → Add Package Dependencies… → Add Local… → choose the `<App>Core` folder and add the `<App>Core` library to the app target.

## Tests

    cd <App>/<App>Core
    swift test

<list what the tests cover>

## Notes

<device requirements, e.g. "tilt control needs a real device; the simulator has no accelerometer", controls, known limits>
```

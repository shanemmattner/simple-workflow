# Device Testing Design — iOS & Android Validation

> **Status**: Design only — not implemented. Build after web validation (validate.md) is proven in production.

## Overview

Extend the validate phase to run on-device tests against physical iOS and Android devices connected to the Mac Studio. This covers native app changes, responsive web behavior on real devices, and platform-specific bugs that browser emulation misses.

## Architecture

```
orchestrator.py
  └── validate phase
        ├── web validation (existing — Playwright against Vercel preview)
        ├── ios validation (new — xcodebuild + XCUITest on connected iPhone)
        └── android validation (new — gradle + Espresso on connected Android via adb)
```

The validate phase becomes a dispatcher: it checks the issue/triage metadata for platform targets and runs the appropriate sub-validations in parallel.

## iOS Validation Flow

### Prerequisites
- Xcode installed on Mac Studio
- iPhone connected via USB or wireless pairing
- Developer signing identity configured
- Target app's Xcode project in the repo

### Steps

1. **Build for device**
   ```bash
   xcodebuild -workspace App.xcworkspace \
     -scheme AppScheme \
     -destination "id=$(xcrun xctrace list devices 2>&1 | grep -m1 'iPhone' | grep -oE '[A-F0-9-]{36}')" \
     -derivedDataPath /tmp/sw-validate-ios \
     build-for-testing
   ```

2. **Install on device**
   ```bash
   xcrun devicectl device install app \
     --device <device-id> \
     /tmp/sw-validate-ios/Build/Products/Debug-iphoneos/App.app
   ```

3. **Run XCUITests**
   ```bash
   xcodebuild test-without-building \
     -workspace App.xcworkspace \
     -scheme AppScheme \
     -destination "id=<device-id>" \
     -derivedDataPath /tmp/sw-validate-ios \
     -only-testing:AppUITests/ValidateTests
   ```
   
   The pipeline would generate a `ValidateTests.swift` file (similar to how test-plan generates test specs) containing XCUITest cases derived from the issue description.

4. **Capture results**
   - Parse xcresult bundle for pass/fail
   - Extract screenshots from the xcresult
   - Report structured results matching the validate schema

### Device Discovery

```bash
# List connected iOS devices
xcrun xctrace list devices 2>&1 | grep -E 'iPhone|iPad'

# Get device UDID
system_profiler SPUSBDataType 2>/dev/null | grep -A5 'iPhone'
```

### Challenges
- **Signing**: requires a valid development certificate. The Mac Studio needs a provisioning profile for the target app.
- **Device availability**: device must be unlocked and trusted. No programmatic unlock.
- **Build time**: iOS builds are slow (2-10 min). Budget must account for this.
- **Test generation**: generating valid XCUITest Swift code from an issue description is harder than web testing — needs knowledge of the app's accessibility identifiers.

## Android Validation Flow

### Prerequisites
- Android SDK installed on Mac Studio
- Android device connected via USB with USB debugging enabled
- Target app's Gradle project in the repo

### Steps

1. **Build debug APK**
   ```bash
   cd /path/to/android/project
   ./gradlew assembleDebug assembleAndroidTest
   ```

2. **Install on device**
   ```bash
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
   ```

3. **Run Espresso tests**
   ```bash
   adb shell am instrument -w \
     -e class com.app.test.ValidateTest \
     com.app.test/androidx.test.runner.AndroidJUnitRunner
   ```
   
   Similar to iOS: pipeline generates a `ValidateTest.kt` Espresso test from the issue description.

4. **Capture results**
   ```bash
   # Pull screenshots
   adb pull /sdcard/Pictures/screenshots/ /tmp/sw-validate-android/

   # Get test results
   adb shell cat /data/data/com.app/files/test-results.xml
   ```

### Device Discovery

```bash
# List connected Android devices
adb devices -l

# Get device info
adb shell getprop ro.product.model
adb shell getprop ro.build.version.sdk
```

### Challenges
- **Emulator fallback**: if no physical device, use Android emulator (slower but always available)
- **Build time**: Android builds are moderate (1-5 min)
- **Test generation**: Espresso tests need view IDs. The pipeline would need the app's view hierarchy.
- **ADB reliability**: adb connections drop. Need retry logic.

## Platform Detection

The orchestrator determines which validations to run based on:

1. **File extensions in triage output**:
   - `.swift`, `.xib`, `.storyboard`, `.xcodeproj` → iOS
   - `.kt`, `.java`, `.xml` (android layouts), `build.gradle` → Android
   - `.tsx`, `.jsx`, `.css`, `.html` → Web (existing)

2. **Repo structure**: presence of `ios/`, `android/`, `Podfile`, `build.gradle`

3. **Issue labels**: `platform:ios`, `platform:android`, `platform:web`

## Validate Phase Dispatch (Future orchestrator.py)

```python
# After review, before improve:
validations = []
if validate.check_has_ui_changes(triage_text, issue_body):
    validations.append("web")
if validate.check_has_ios_changes(triage_text):
    validations.append("ios")
if validate.check_has_android_changes(triage_text):
    validations.append("android")

# Run applicable validations (web in parallel with device)
results = {}
if "web" in validations:
    results["web"] = run_web_validation(pr_url, preview_url)
if "ios" in validations:
    results["ios"] = run_ios_validation(worktree_path, device_id)
if "android" in validations:
    results["android"] = run_android_validation(worktree_path, device_id)
```

## Implementation Order

1. **v1 (now)**: Web validation via Playwright against Vercel preview — `validate.md` + `validate.py`
2. **v2**: iOS validation on Mac Studio with connected iPhone — needs xcodebuild integration
3. **v3**: Android validation with adb — needs Android SDK on Mac Studio
4. **v4**: Emulator fallback for both platforms when no physical device connected

## Cost Estimate

| Platform | Build Time | Test Time | Total per Run |
|----------|-----------|-----------|---------------|
| Web      | 0 (Vercel builds) | 1-2 min | 1-2 min |
| iOS      | 2-10 min  | 1-3 min   | 3-13 min |
| Android  | 1-5 min   | 1-2 min   | 2-7 min |

Budget impact: web validation adds ~$0.05-0.10 per run (haiku agent, few turns). Device validation would add build time but minimal LLM cost (results are parsed programmatically).

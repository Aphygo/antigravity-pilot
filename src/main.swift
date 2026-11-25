import Cocoa
import ApplicationServices
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// MARK: - Constants & Paths

let REFS_FILE_PATH = "/tmp/macos-ctl-refs.json"
let ACCESSIBILITY_PREFS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
let SCREEN_RECORDING_PREFS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
let INPUT_MONITORING_PREFS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

var GLOBAL_CONFIRM_RISKY: Bool = false
var GLOBAL_TRACE_PATH: String? = {
    if let env = ProcessInfo.processInfo.environment["MACOS_CTL_TRACE"], !env.isEmpty {
        return NSString(string: env).expandingTildeInPath
    }
    return nil
}()
var GLOBAL_TRACE_COMMAND: String = ""
var GLOBAL_TRACE_ARGS: [String] = []
var GLOBAL_START_TIME: CFAbsoluteTime = CFAbsoluteTimeGetCurrent()
var GLOBAL_RISK_BLOCKED: Bool = false
var GLOBAL_TRACE_WRITTEN: Bool = false

let RISKY_KEYWORDS: [String] = [
    "confirm payment", "submit order", "empty trash", "töm papperskorgen", "ta bort",
    "send", "skicka", "publish", "publicera", "post",
    "delete", "radera", "remove",
    "pay", "betala", "purchase", "köp", "transfer", "överför",
    "sign", "signera", "erase", "format"
]

struct TraceEntry: Codable {
    let timestamp: String
    let command: String
    let args: [String]
    let risk_blocked: Bool
    let exit_code: Int32
    let duration_ms: Int
}

struct RiskGuardError: Codable {
    let code: String
    let action: String
    let matched_keyword: String
    let target: String
    let hint: String
}

func writeTrace(exitCode: Int32) {
    guard !GLOBAL_TRACE_WRITTEN, let tracePath = GLOBAL_TRACE_PATH, !tracePath.isEmpty else { return }
    GLOBAL_TRACE_WRITTEN = true

    let durationMs = Int(round((CFAbsoluteTimeGetCurrent() - GLOBAL_START_TIME) * 1000.0))
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let entry = TraceEntry(
        timestamp: formatter.string(from: Date()),
        command: GLOBAL_TRACE_COMMAND,
        args: GLOBAL_TRACE_ARGS,
        risk_blocked: GLOBAL_RISK_BLOCKED,
        exit_code: exitCode,
        duration_ms: durationMs
    )

    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    guard let data = try? encoder.encode(entry),
          var line = String(data: data, encoding: .utf8) else { return }
    line.append("\n")
    guard let lineData = line.data(using: .utf8) else { return }

    let url = URL(fileURLWithPath: tracePath)
    let dir = url.deletingLastPathComponent()
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

    if FileManager.default.fileExists(atPath: tracePath) {
        if let fh = try? FileHandle(forWritingTo: url) {
            fh.seekToEndOfFile()
            fh.write(lineData)
            try? fh.close()
        }
    } else {
        try? lineData.write(to: url)
    }
}

func exitWithTrace(_ code: Int32) -> Never {
    writeTrace(exitCode: code)
    exit(code)
}

func matchRiskyKeyword(in text: String) -> String? {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    let range = NSRange(trimmed.startIndex..<trimmed.endIndex, in: trimmed)
    for kw in RISKY_KEYWORDS {
        let escaped = NSRegularExpression.escapedPattern(for: kw)
        let pattern = "(?<![\\p{L}\\p{N}_])" + escaped + "(?![\\p{L}\\p{N}_])"
        if let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
           regex.firstMatch(in: trimmed, options: [], range: range) != nil {
            return kw
        }
    }
    return nil
}

func evaluateRiskGuard(action: String, targetLabel: String) -> String? {
    let act = action.lowercased()
    if act == "quit --force" || act == "quit_force" {
        return "quit --force"
    }
    if act == "press" || act == "menu" || act == "recipe" {
        return matchRiskyKeyword(in: targetLabel)
    }
    return nil
}

func checkRiskGuard(action: String, targetLabel: String, confirmRisky: Bool = GLOBAL_CONFIRM_RISKY) {
    guard let matched = evaluateRiskGuard(action: action, targetLabel: targetLabel) else {
        return
    }
    if confirmRisky {
        return
    }
    GLOBAL_RISK_BLOCKED = true
    let payload = RiskGuardError(
        code: "RISK_GUARD_BLOCKED",
        action: action,
        matched_keyword: matched,
        target: targetLabel,
        hint: "Re-run with --confirm-risky only after explicit user approval"
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(payload), let str = String(data: data, encoding: .utf8) {
        printErr(str)
    } else {
        printErr("{\"code\":\"RISK_GUARD_BLOCKED\",\"matched_keyword\":\"\(matched)\",\"target\":\"\(targetLabel)\"}")
    }
    exitWithTrace(2)
}

let INTERACTIVE_ROLES: Set<String> = [
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton",
    "AXLink", "AXTabButton", "AXTab", "AXMenuItem",
    "AXSlider", "AXComboBox", "AXRow", "AXCell"
]

let CONTAINER_ROLES: Set<String> = [
    "AXGroup", "AXSplitGroup", "AXScrollArea", "AXLayoutArea",
    "AXGenericElement", "AXWebArea", "AXUnknown", "AXList", "AXOutline", "AXTable"
]

// MARK: - Output Helpers

func printErr(_ msg: String) {
    FileHandle.standardError.write(Data((msg + "\n").utf8))
}

func fail(_ msg: String, code: Int32 = 1) -> Never {
    printErr("Error: \(msg)")
    exitWithTrace(code)
}

func printJSON<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    do {
        let data = try encoder.encode(value)
        if let str = String(data: data, encoding: .utf8) {
            print(str)
        }
    } catch {
        fail("Failed to encode JSON: \(error)")
    }
}

// MARK: - Process Helper

struct ProcessResult {
    let status: Int32
    let stdout: String
    let stderr: String
}

func runProcess(_ executable: String, args: [String], stdinData: Data? = nil) -> ProcessResult {
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: executable)
    proc.arguments = args

    let outPipe = Pipe()
    let errPipe = Pipe()
    proc.standardOutput = outPipe
    proc.standardError = errPipe

    if let input = stdinData {
        let inPipe = Pipe()
        proc.standardInput = inPipe
        do {
            try proc.run()
        } catch {
            return ProcessResult(status: -1, stdout: "", stderr: error.localizedDescription)
        }
        inPipe.fileHandleForWriting.write(input)
        inPipe.fileHandleForWriting.closeFile()
    } else {
        do {
            try proc.run()
        } catch {
            return ProcessResult(status: -1, stdout: "", stderr: error.localizedDescription)
        }
    }

    let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
    let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
    proc.waitUntilExit()

    let stdout = String(data: outData, encoding: .utf8) ?? ""
    let stderr = String(data: errData, encoding: .utf8) ?? ""
    return ProcessResult(status: proc.terminationStatus, stdout: stdout, stderr: stderr)
}

// MARK: - Argument Parsing Helpers

struct ParsedArgs {
    var positionals: [String] = []
    var flags: Set<String> = []
    var options: [String: String] = [:]
    var multiOptions: [String: [String]] = [:]

    func hasFlag(_ names: String...) -> Bool {
        for n in names {
            if flags.contains(n) { return true }
        }
        return false
    }

    func option(_ names: String...) -> String? {
        for n in names {
            if let v = options[n] { return v }
        }
        return nil
    }

    func optionsList(_ names: String...) -> [String] {
        var out: [String] = []
        for n in names {
            if let list = multiOptions[n] {
                out.append(contentsOf: list)
            }
        }
        return out
    }
}

func parseArgs(_ args: [String], valueOptions: Set<String> = []) -> ParsedArgs {
    var res = ParsedArgs()
    var i = 0
    while i < args.count {
        let arg = args[i]
        if arg == "--" {
            if i + 1 < args.count {
                res.positionals.append(contentsOf: args[(i + 1)...])
            }
            break
        }
        if valueOptions.contains(arg) {
            if i + 1 < args.count {
                res.options[arg] = args[i + 1]
                res.multiOptions[arg, default: []].append(args[i + 1])
                i += 2
                continue
            } else {
                fail("Option \(arg) requires a value.")
            }
        }
        if arg.hasPrefix("--") {
            if let eqIdx = arg.firstIndex(of: "=") {
                let key = String(arg[..<eqIdx])
                let val = String(arg[arg.index(after: eqIdx)...])
                res.options[key] = val
                res.multiOptions[key, default: []].append(val)
            } else {
                res.flags.insert(arg)
            }
        } else if arg.hasPrefix("-") && arg.count == 2 && !arg.dropFirst().allSatisfy({ $0.isNumber }) {
            res.flags.insert(arg)
        } else {
            res.positionals.append(arg)
        }
        i += 1
    }
    return res
}

// MARK: - App Resolution Helpers

func findRunningApp(named query: String) -> NSRunningApplication? {
    let q = query.lowercased()
    let apps = NSWorkspace.shared.runningApplications

    // 1. Exact match on regular apps (name or bundleId)
    if let match = apps.first(where: {
        $0.activationPolicy == .regular &&
        ($0.localizedName?.lowercased() == q || $0.bundleIdentifier?.lowercased() == q)
    }) {
        return match
    }
    // 2. Exact match on any app
    if let match = apps.first(where: {
        $0.localizedName?.lowercased() == q || $0.bundleIdentifier?.lowercased() == q
    }) {
        return match
    }
    // 3. Substring match on regular apps
    if let match = apps.first(where: {
        $0.activationPolicy == .regular &&
        (($0.localizedName?.lowercased().contains(q) ?? false) ||
         ($0.bundleIdentifier?.lowercased().contains(q) ?? false))
    }) {
        return match
    }
    // 4. Substring match on any app
    return apps.first(where: {
        ($0.localizedName?.lowercased().contains(q) ?? false) ||
        ($0.bundleIdentifier?.lowercased().contains(q) ?? false)
    })
}

func resolveTargetApp(appName: String?) -> NSRunningApplication {
    if let name = appName {
        guard let app = findRunningApp(named: name) else {
            fail("Running application '\(name)' not found.")
        }
        return app
    }
    guard let front = NSWorkspace.shared.frontmostApplication else {
        fail("No frontmost application found.")
    }
    return front
}

func activateApp(_ app: NSRunningApplication) {
    if app.isHidden {
        app.unhide()
    }
    if #available(macOS 14.0, *) {
        app.activate()
    } else {
        app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
    }
}

func requireAccessibility() {
    if !AXIsProcessTrusted() {
        fail("""
        Accessibility permission is not granted.
        Grant permission to your terminal/runner in System Settings:
          open "\(ACCESSIBILITY_PREFS_URL)"
        """)
    }
}

// MARK: - AX Helpers

func axCopyAttribute(_ element: AXUIElement, _ attr: String) -> AnyObject? {
    var value: AnyObject?
    let err = AXUIElementCopyAttributeValue(element, attr as CFString, &value)
    guard err == .success else { return nil }
    return value
}

func axStringAttribute(_ element: AXUIElement, _ attr: String) -> String? {
    guard let val = axCopyAttribute(element, attr) else { return nil }
    if let s = val as? String { return s }
    if let n = val as? NSNumber { return n.stringValue }
    return nil
}

func axChildren(_ element: AXUIElement) -> [AXUIElement] {
    guard let val = axCopyAttribute(element, kAXChildrenAttribute) as? [AXUIElement] else {
        return []
    }
    return val
}

func axActions(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    let err = AXUIElementCopyActionNames(element, &names)
    guard err == .success, let arr = names as? [String] else { return [] }
    return arr
}

func axPosition(_ element: AXUIElement) -> CGPoint? {
    guard let val = axCopyAttribute(element, kAXPositionAttribute) else { return nil }
    var pt = CGPoint.zero
    if AXValueGetValue(val as! AXValue, .cgPoint, &pt) {
        return pt
    }
    return nil
}

func axSize(_ element: AXUIElement) -> CGSize? {
    guard let val = axCopyAttribute(element, kAXSizeAttribute) else { return nil }
    var sz = CGSize.zero
    if AXValueGetValue(val as! AXValue, .cgSize, &sz) {
        return sz
    }
    return nil
}

func axSetPosition(_ element: AXUIElement, _ point: CGPoint) -> AXError {
    var pt = point
    guard let val = AXValueCreate(.cgPoint, &pt) else { return .failure }
    return AXUIElementSetAttributeValue(element, kAXPositionAttribute as CFString, val)
}

func axSetSize(_ element: AXUIElement, _ size: CGSize) -> AXError {
    var sz = size
    guard let val = AXValueCreate(.cgSize, &sz) else { return .failure }
    return AXUIElementSetAttributeValue(element, kAXSizeAttribute as CFString, val)
}

func axFrame(_ element: AXUIElement) -> CGRect? {
    guard let pos = axPosition(element), let sz = axSize(element) else { return nil }
    return CGRect(origin: pos, size: sz)
}

func axElementByPath(pid: pid_t, path: [Int]) -> AXUIElement? {
    var current = AXUIElementCreateApplication(pid)
    for idx in path {
        let kids = axChildren(current)
        guard idx >= 0 && idx < kids.count else { return nil }
        current = kids[idx]
    }
    return current
}

func axFindWindow(for app: NSRunningApplication, titleSubstr: String?) -> AXUIElement {
    let appEl = AXUIElementCreateApplication(app.processIdentifier)
    let windows = (axCopyAttribute(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? []

    if let substr = titleSubstr?.lowercased(), !substr.isEmpty {
        if let matched = windows.first(where: {
            (axStringAttribute($0, kAXTitleAttribute)?.lowercased().contains(substr) ?? false)
        }) {
            return matched
        }
        fail("No window matching title '\(titleSubstr!)' found for app '\(app.localizedName ?? "")'.")
    }

    if let focused = axCopyAttribute(appEl, kAXFocusedWindowAttribute) {
        return (focused as! AXUIElement)
    }
    if let mainWin = axCopyAttribute(appEl, kAXMainWindowAttribute) {
        return (mainWin as! AXUIElement)
    }
    if let first = windows.first {
        return first
    }
    fail("No accessible windows found for app '\(app.localizedName ?? "")'.")
}

// MARK: - 1. Doctor

struct ScreenInfo: Codable {
    let index: Int
    let name: String
    let isMain: Bool
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let visibleX: Double
    let visibleY: Double
    let visibleWidth: Double
    let visibleHeight: Double
    let backingScaleFactor: Double
}

struct FrontmostInfo: Codable {
    let name: String
    let bundleId: String
    let pid: Int32
}

struct DoctorReport: Codable {
    let accessibility: Bool
    let screen_recording: Bool
    let input_monitoring: Bool
    let macos_version: String
    let frontmost_app: FrontmostInfo?
    let screens: [ScreenInfo]
    let guidance: [String]
}

func cmdDoctor(_ args: [String]) {
    let parsed = parseArgs(args)
    let asJson = parsed.hasFlag("--json")

    let axTrusted = AXIsProcessTrusted()
    let screenCap = CGPreflightScreenCaptureAccess()
    let inputMon = CGPreflightListenEventAccess()
    let osVersion = ProcessInfo.processInfo.operatingSystemVersionString

    let mainScreen = NSScreen.main
    let screens: [ScreenInfo] = NSScreen.screens.enumerated().map { idx, s in
        let f = s.frame
        let vf = s.visibleFrame
        return ScreenInfo(
            index: idx,
            name: s.localizedName,
            isMain: s == mainScreen,
            x: Double(f.origin.x),
            y: Double(f.origin.y),
            width: Double(f.size.width),
            height: Double(f.size.height),
            visibleX: Double(vf.origin.x),
            visibleY: Double(vf.origin.y),
            visibleWidth: Double(vf.size.width),
            visibleHeight: Double(vf.size.height),
            backingScaleFactor: Double(s.backingScaleFactor)
        )
    }

    let front: FrontmostInfo? = NSWorkspace.shared.frontmostApplication.map {
        FrontmostInfo(
            name: $0.localizedName ?? "Unknown",
            bundleId: $0.bundleIdentifier ?? "",
            pid: $0.processIdentifier
        )
    }

    var guidance: [String] = []
    if !axTrusted {
        guidance.append("Accessibility permission is missing. Open: \(ACCESSIBILITY_PREFS_URL)")
    }
    if !screenCap {
        guidance.append("Screen Recording permission is missing. Open: \(SCREEN_RECORDING_PREFS_URL)")
    }
    if !inputMon {
        guidance.append("Input Monitoring permission is missing (needed only for 'record'). Open: \(INPUT_MONITORING_PREFS_URL)")
    }

    let report = DoctorReport(
        accessibility: axTrusted,
        screen_recording: screenCap,
        input_monitoring: inputMon,
        macos_version: osVersion,
        frontmost_app: front,
        screens: screens,
        guidance: guidance
    )

    if asJson {
        printJSON(report)
        return
    }

    print("macos-ctl doctor")
    print("================")
    print("macOS:            \(osVersion)")
    print("Accessibility:    \(axTrusted ? "OK" : "MISSING")")
    print("Screen Recording: \(screenCap ? "OK" : "MISSING")")
    print("Input Monitoring: \(inputMon ? "OK" : "MISSING")")
    if let f = front {
        print("Frontmost App:    \(f.name) (\(f.bundleId), pid \(f.pid))")
    } else {
        print("Frontmost App:    None")
    }
    print("Screens (\(screens.count)):")
    for s in screens {
        let tag = s.isMain ? " [main]" : ""
        print("  [\(s.index)] \(s.name)\(tag): \(Int(s.width))x\(Int(s.height)) @(\(Int(s.x)),\(Int(s.y))) scale=\(s.backingScaleFactor)x")
    }
    if !guidance.isEmpty {
        print("\nAction required:")
        for g in guidance {
            print("  - \(g)")
        }
    }
}

// MARK: - 2. Apps

struct AppInfo: Codable {
    let name: String
    let bundleId: String
    let pid: Int32
    let active: Bool
    let hidden: Bool
}

func cmdApps(_ args: [String]) {
    let parsed = parseArgs(args)
    let asJson = parsed.hasFlag("--json")

    let list: [AppInfo] = NSWorkspace.shared.runningApplications
        .filter { $0.activationPolicy == .regular }
        .map {
            AppInfo(
                name: $0.localizedName ?? "Unknown",
                bundleId: $0.bundleIdentifier ?? "",
                pid: $0.processIdentifier,
                active: $0.isActive,
                hidden: $0.isHidden
            )
        }
        .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }

    if asJson {
        printJSON(list)
        return
    }

    for a in list {
        var flags: [String] = []
        if a.active { flags.append("active") }
        if a.hidden { flags.append("hidden") }
        let suffix = flags.isEmpty ? "" : " [\(flags.joined(separator: ", "))]"
        print("\(a.name) (\(a.bundleId)) pid=\(a.pid)\(suffix)")
    }
}

// MARK: - 3. Open

func cmdOpen(_ args: [String]) {
    guard !args.isEmpty else {
        fail("Usage: macos-ctl open <target>")
    }
    let target = args.joined(separator: " ")

    // Check URL scheme (http://, https://, x-apple.systempreferences:, etc.)
    let hasScheme: Bool = {
        guard let colonIdx = target.firstIndex(of: ":") else { return false }
        let scheme = String(target[..<colonIdx])
        guard !scheme.isEmpty, scheme.first!.isLetter else { return false }
        return scheme.allSatisfy { $0.isLetter || $0.isNumber || $0 == "+" || $0 == "." || $0 == "-" }
    }()

    if hasScheme, let url = URL(string: target) {
        if NSWorkspace.shared.open(url) {
            print("Opened URL: \(target)")
            return
        }
        let res = runProcess("/usr/bin/open", args: [target])
        if res.status == 0 {
            print("Opened URL: \(target)")
            return
        }
        fail("Failed to open URL '\(target)': \(res.stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
    }

    // Check existing file path
    let expandedPath = NSString(string: target).expandingTildeInPath
    if FileManager.default.fileExists(atPath: expandedPath) {
        let url = URL(fileURLWithPath: expandedPath)
        if NSWorkspace.shared.open(url) {
            print("Opened path: \(expandedPath)")
            return
        }
        let res = runProcess("/usr/bin/open", args: [expandedPath])
        if res.status == 0 {
            print("Opened path: \(expandedPath)")
            return
        }
        fail("Failed to open path '\(expandedPath)': \(res.stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
    }

    // Otherwise open macOS app
    var res = runProcess("/usr/bin/open", args: ["-a", target])
    if res.status != 0 && target.contains(".") {
        res = runProcess("/usr/bin/open", args: ["-b", target])
    }
    if res.status == 0 {
        print("Opened app: \(target)")
    } else {
        fail("Failed to open '\(target)': \(res.stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
    }
}

// MARK: - 4. Focus

func cmdFocus(_ args: [String]) {
    guard !args.isEmpty else {
        fail("Usage: macos-ctl focus <app>")
    }
    let query = args.joined(separator: " ")
    guard let app = findRunningApp(named: query) else {
        fail("Running application '\(query)' not found.")
    }
    activateApp(app)
    print("Focused: \(app.localizedName ?? query) (pid \(app.processIdentifier))")
}

// MARK: - 5. Quit

func cmdQuit(_ args: [String]) {
    let parsed = parseArgs(args)
    guard !parsed.positionals.isEmpty else {
        fail("Usage: macos-ctl quit <app> [--force]")
    }
    let force = parsed.hasFlag("--force", "-f")
    let query = parsed.positionals.joined(separator: " ")
    if force {
        checkRiskGuard(action: "quit --force", targetLabel: query)
    }
    guard let app = findRunningApp(named: query) else {
        fail("Running application '\(query)' not found.")
    }
    let ok = force ? app.forceTerminate() : app.terminate()
    if ok {
        print("\(force ? "Force-terminated" : "Terminated"): \(app.localizedName ?? query) (pid \(app.processIdentifier))")
    } else {
        fail("Failed to terminate '\(app.localizedName ?? query)'.")
    }
}

// MARK: - 6. Windows

struct WindowInfo: Codable {
    let app: String
    let pid: Int32
    let title: String
    let windowId: UInt32
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

func listWindows(appFilter: String?) -> [WindowInfo] {
    guard let rawList = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
        return []
    }

    var targetPid: Int32? = nil
    let filterLower = appFilter?.lowercased()
    if let filter = appFilter, let matchedApp = findRunningApp(named: filter) {
        targetPid = matchedApp.processIdentifier
    }

    let axTrusted = AXIsProcessTrusted()
    var axTitleCache: [Int32: [(CGRect, String)]] = [:]

    func lookupAXTitle(pid: Int32, rect: CGRect) -> String? {
        guard axTrusted else { return nil }
        if axTitleCache[pid] == nil {
            let appEl = AXUIElementCreateApplication(pid)
            let wins = (axCopyAttribute(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? []
            var entries: [(CGRect, String)] = []
            for w in wins {
                if let f = axFrame(w), let t = axStringAttribute(w, kAXTitleAttribute), !t.isEmpty {
                    entries.append((f, t))
                }
            }
            axTitleCache[pid] = entries
        }
        guard let entries = axTitleCache[pid] else { return nil }
        return entries.first(where: {
            abs($0.0.origin.x - rect.origin.x) < 8 &&
            abs($0.0.origin.y - rect.origin.y) < 8 &&
            abs($0.0.size.width - rect.size.width) < 8
        })?.1
    }

    var results: [WindowInfo] = []
    for dict in rawList {
        let layer = (dict[kCGWindowLayer as String] as? Int) ?? 0
        if layer != 0 { continue }

        let ownerName = (dict[kCGWindowOwnerName as String] as? String) ?? ""
        let pid = Int32((dict[kCGWindowOwnerPID as String] as? Int) ?? 0)
        let winId = UInt32((dict[kCGWindowNumber as String] as? Int) ?? 0)

        if let filter = filterLower {
            let pidMatch = (targetPid != nil && pid == targetPid!)
            let nameMatch = ownerName.lowercased().contains(filter)
            if !pidMatch && !nameMatch { continue }
        }

        guard let boundsDict = dict[kCGWindowBounds as String] as? [String: Any],
              let rect = CGRect(dictionaryRepresentation: boundsDict as CFDictionary),
              rect.width > 1 && rect.height > 1 else {
            continue
        }

        var title = (dict[kCGWindowName as String] as? String) ?? ""
        if title.isEmpty, let axT = lookupAXTitle(pid: pid, rect: rect) {
            title = axT
        }

        results.append(WindowInfo(
            app: ownerName,
            pid: pid,
            title: title,
            windowId: winId,
            x: Double(rect.origin.x),
            y: Double(rect.origin.y),
            width: Double(rect.size.width),
            height: Double(rect.size.height)
        ))
    }
    return results
}

func cmdWindows(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--app"])
    let appFilter = parsed.option("--app")
    let asJson = parsed.hasFlag("--json")

    let wins = listWindows(appFilter: appFilter)
    if asJson {
        printJSON(wins)
        return
    }
    if wins.isEmpty {
        print("No visible windows found.")
        return
    }
    for w in wins {
        let titlePart = w.title.isEmpty ? "(untitled)" : "\"\(w.title)\""
        print("[\(w.windowId)] \(w.app) (pid \(w.pid)) \(titlePart) @(\(Int(w.x)), \(Int(w.y))) [\(Int(w.width))x\(Int(w.height))]")
    }
}

// MARK: - 7. Window Management

func cmdWindowMove(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--title"])
    guard parsed.positionals.count >= 3,
          let x = Double(parsed.positionals[1]),
          let y = Double(parsed.positionals[2]) else {
        fail("Usage: macos-ctl window-move <app> <x> <y> [--title <substr>]")
    }
    let app = resolveTargetApp(appName: parsed.positionals[0])
    let win = axFindWindow(for: app, titleSubstr: parsed.option("--title"))
    let err = axSetPosition(win, CGPoint(x: x, y: y))
    if err != .success {
        fail("Failed to move window (AXError \(err.rawValue)).")
    }
    print("Moved '\(app.localizedName ?? parsed.positionals[0])' window to (\(Int(x)), \(Int(y))).")
}

func cmdWindowResize(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--title"])
    guard parsed.positionals.count >= 3,
          let w = Double(parsed.positionals[1]),
          let h = Double(parsed.positionals[2]) else {
        fail("Usage: macos-ctl window-resize <app> <w> <h> [--title <substr>]")
    }
    let app = resolveTargetApp(appName: parsed.positionals[0])
    let win = axFindWindow(for: app, titleSubstr: parsed.option("--title"))
    let err = axSetSize(win, CGSize(width: w, height: h))
    if err != .success {
        fail("Failed to resize window (AXError \(err.rawValue)).")
    }
    print("Resized '\(app.localizedName ?? parsed.positionals[0])' window to \(Int(w))x\(Int(h)).")
}

func cmdWindowTile(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--title", "--display"])
    guard parsed.positionals.count >= 2 else {
        fail("Usage: macos-ctl window-tile <app> <left|right|top|bottom|maximize|center> [--title <substr>] [--display <1-based>]")
    }
    let appName = parsed.positionals[0]
    let layout = parsed.positionals[1].lowercased()
    let app = resolveTargetApp(appName: appName)
    let win = axFindWindow(for: app, titleSubstr: parsed.option("--title"))

    let allScreens = NSScreen.screens
    let screen: NSScreen
    if let dispStr = parsed.option("--display") {
        guard let dispIdx = Int(dispStr), dispIdx >= 1, dispIdx <= allScreens.count else {
            fail("Invalid --display '\(dispStr)'. Available displays: 1..\(allScreens.count).")
        }
        screen = allScreens[dispIdx - 1]
    } else {
        guard let fallback = NSScreen.main ?? allScreens.first else {
            fail("No screen available.")
        }
        screen = fallback
    }
    let primaryHeight = allScreens.first?.frame.height ?? screen.frame.height
    let vf = screen.visibleFrame

    // Convert AppKit bottom-left coordinates to AX/CG top-left coordinates
    let axX = vf.origin.x
    let axY = primaryHeight - (vf.origin.y + vf.size.height)
    let axW = vf.size.width
    let axH = vf.size.height

    let targetRect: CGRect
    switch layout {
    case "left":
        targetRect = CGRect(x: axX, y: axY, width: floor(axW / 2), height: axH)
    case "right":
        let halfW = floor(axW / 2)
        targetRect = CGRect(x: axX + halfW, y: axY, width: axW - halfW, height: axH)
    case "top":
        targetRect = CGRect(x: axX, y: axY, width: axW, height: floor(axH / 2))
    case "bottom":
        let halfH = floor(axH / 2)
        targetRect = CGRect(x: axX, y: axY + halfH, width: axW, height: axH - halfH)
    case "maximize", "max", "full":
        targetRect = CGRect(x: axX, y: axY, width: axW, height: axH)
    case "center":
        let curSize = axSize(win) ?? CGSize(width: floor(axW * 0.7), height: floor(axH * 0.7))
        let w = min(curSize.width, axW)
        let h = min(curSize.height, axH)
        targetRect = CGRect(
            x: axX + floor((axW - w) / 2),
            y: axY + floor((axH - h) / 2),
            width: w,
            height: h
        )
    default:
        fail("Unknown layout '\(layout)'. Use: left, right, top, bottom, maximize, center.")
    }

    _ = axSetPosition(win, targetRect.origin)
    _ = axSetSize(win, targetRect.size)
    _ = axSetPosition(win, targetRect.origin)
    print("Tiled '\(app.localizedName ?? appName)' to \(layout): @(\(Int(targetRect.origin.x)), \(Int(targetRect.origin.y))) [\(Int(targetRect.size.width))x\(Int(targetRect.size.height))]")
}

// MARK: - 8. Semantic Accessibility Tree & Refs

struct RefElement: Codable {
    let ref: String
    let role: String
    let title: String
    let description: String
    let value: String
    let identifier: String
    let x: Int
    let y: Int
    let w: Int
    let h: Int
    let path: [Int]
}

struct StoredRefElement: Codable {
    let role: String
    let title: String
    let x: Int
    let y: Int
    let w: Int
    let h: Int
    let path: [Int]
}

struct RefsFile: Codable {
    let app: String
    let pid: Int32
    let elements: [String: StoredRefElement]
}

func saveRefsFile(app: NSRunningApplication, elements: [RefElement]) {
    var map: [String: StoredRefElement] = [:]
    for el in elements {
        map[el.ref] = StoredRefElement(
            role: el.role,
            title: el.title,
            x: el.x,
            y: el.y,
            w: el.w,
            h: el.h,
            path: el.path
        )
    }
    let payload = RefsFile(
        app: app.localizedName ?? "Unknown",
        pid: app.processIdentifier,
        elements: map
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(payload) {
        try? data.write(to: URL(fileURLWithPath: REFS_FILE_PATH))
    }
}

func loadRefsFile() -> RefsFile? {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: REFS_FILE_PATH)) else {
        return nil
    }
    return try? JSONDecoder().decode(RefsFile.self, from: data)
}

func cleanLabel(_ s: String?, maxLen: Int = 80) -> String {
    guard let raw = s else { return "" }
    let trimmed = raw
        .replacingOccurrences(of: "\n", with: " ")
        .replacingOccurrences(of: "\r", with: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.count > maxLen {
        return String(trimmed.prefix(maxLen - 1)) + "…"
    }
    return trimmed
}

func collectAXElements(
    app: NSRunningApplication,
    maxDepth: Int,
    interactiveOnly: Bool,
    limit: Int,
    query: String? = nil,
    roleFilter: String? = nil,
    maxVisited: Int = 2000,
    timeoutSec: Double = 3.0
) -> [RefElement] {
    let root = AXUIElementCreateApplication(app.processIdentifier)
    var results: [RefElement] = []
    let queryLower = query?.lowercased()
    let roleLower = roleFilter?.lowercased()
    var visited = 0
    let startTime = CFAbsoluteTimeGetCurrent()
    var timedOut = false

    func shouldStop() -> Bool {
        if results.count >= limit || visited >= maxVisited || timedOut { return true }
        if visited & 31 == 0 && (CFAbsoluteTimeGetCurrent() - startTime) >= timeoutSec {
            timedOut = true
            return true
        }
        return false
    }

    func traverse(_ el: AXUIElement, depth: Int, path: [Int]) {
        if shouldStop() { return }
        visited += 1

        if !path.isEmpty {
            let role = axStringAttribute(el, kAXRoleAttribute) ?? "AXUnknown"
            let title = cleanLabel(axStringAttribute(el, kAXTitleAttribute))
            let desc = cleanLabel(axStringAttribute(el, kAXDescriptionAttribute))
            let valStr = cleanLabel(axStringAttribute(el, kAXValueAttribute))
            let help = cleanLabel(axStringAttribute(el, kAXHelpAttribute))
            let ident = cleanLabel(axStringAttribute(el, kAXIdentifierAttribute))

            var include = true

            if let rf = roleLower {
                if role.lowercased() != rf { include = false }
            }

            if include, let q = queryLower {
                let haystacks = [title, desc, valStr, help, ident, role]
                if !haystacks.contains(where: { $0.lowercased().contains(q) }) {
                    include = false
                }
            } else if include && interactiveOnly {
                if INTERACTIVE_ROLES.contains(role) {
                    include = true
                } else if role == "AXStaticText" {
                    let acts = axActions(el)
                    include = acts.contains(kAXPressAction) || acts.contains("AXConfirm")
                } else {
                    include = false
                }
            } else if include && queryLower == nil {
                // Skip unlabeled pure layout containers so we don't flood output
                if CONTAINER_ROLES.contains(role) && title.isEmpty && desc.isEmpty && valStr.isEmpty && ident.isEmpty {
                    include = false
                }
            }

            if include {
                let frame = axFrame(el)
                let w = Int(round(frame?.size.width ?? 0))
                let h = Int(round(frame?.size.height ?? 0))
                let cx = Int(round((frame?.origin.x ?? 0) + (frame?.size.width ?? 0) / 2.0))
                let cy = Int(round((frame?.origin.y ?? 0) + (frame?.size.height ?? 0) / 2.0))

                let displayTitle = !title.isEmpty ? title : (!desc.isEmpty ? desc : (!valStr.isEmpty ? valStr : ident))

                // Skip zero-size unlabeled interactive ghosts
                if !(interactiveOnly && w == 0 && h == 0 && displayTitle.isEmpty) {
                    let refId = "e\(results.count + 1)"
                    results.append(RefElement(
                        ref: refId,
                        role: role,
                        title: displayTitle,
                        description: desc,
                        value: valStr,
                        identifier: ident,
                        x: cx,
                        y: cy,
                        w: w,
                        h: h,
                        path: path
                    ))
                    if results.count >= limit { return }
                }
            }
        }

        if depth >= maxDepth || shouldStop() { return }
        let kids = axChildren(el)
        for (idx, child) in kids.enumerated() {
            if shouldStop() { return }
            var nextPath = path
            nextPath.append(idx)
            traverse(child, depth: depth + 1, path: nextPath)
        }
    }

    traverse(root, depth: 0, path: [])
    return results
}

func formatRefElement(_ el: RefElement) -> String {
    let labelPart = el.title.isEmpty ? "" : " \"\(el.title)\""
    return "[\(el.ref)] \(el.role)\(labelPart) @(\(el.x), \(el.y)) [\(el.w)x\(el.h)]"
}

func cmdTree(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--app", "--depth", "--limit"])
    let app = resolveTargetApp(appName: parsed.option("--app"))
    let depth = min(max(Int(parsed.option("--depth") ?? "6") ?? 6, 1), 12)
    let limit = min(max(Int(parsed.option("--limit") ?? "150") ?? 150, 1), 1000)
    let interactive = parsed.hasFlag("--interactive", "-i")
    let asJson = parsed.hasFlag("--json")

    let elements = collectAXElements(
        app: app,
        maxDepth: depth,
        interactiveOnly: interactive,
        limit: limit
    )
    saveRefsFile(app: app, elements: elements)

    if asJson {
        struct TreeOutput: Codable {
            let app: String
            let pid: Int32
            let count: Int
            let elements: [RefElement]
        }
        printJSON(TreeOutput(
            app: app.localizedName ?? "Unknown",
            pid: app.processIdentifier,
            count: elements.count,
            elements: elements
        ))
        return
    }

    print("# \(app.localizedName ?? "Unknown") (pid \(app.processIdentifier)) — \(elements.count) elements")
    for el in elements {
        print(formatRefElement(el))
    }
}

func cmdFind(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--app", "--role", "--depth", "--limit"])
    guard !parsed.positionals.isEmpty else {
        fail("Usage: macos-ctl find <query> [--app <name>] [--role <AXRole>] [--json]")
    }
    let query = parsed.positionals.joined(separator: " ")
    let app = resolveTargetApp(appName: parsed.option("--app"))
    let roleFilter = parsed.option("--role")
    let depth = min(max(Int(parsed.option("--depth") ?? "6") ?? 6, 1), 12)
    let limit = min(max(Int(parsed.option("--limit") ?? "100") ?? 100, 1), 500)
    let asJson = parsed.hasFlag("--json")

    let elements = collectAXElements(
        app: app,
        maxDepth: depth,
        interactiveOnly: false,
        limit: limit,
        query: query,
        roleFilter: roleFilter
    )
    saveRefsFile(app: app, elements: elements)

    if asJson {
        struct FindOutput: Codable {
            let app: String
            let pid: Int32
            let query: String
            let count: Int
            let elements: [RefElement]
        }
        printJSON(FindOutput(
            app: app.localizedName ?? "Unknown",
            pid: app.processIdentifier,
            query: query,
            count: elements.count,
            elements: elements
        ))
        return
    }

    if elements.isEmpty {
        print("No elements matching '\(query)' in \(app.localizedName ?? "Unknown").")
        return
    }
    print("# Matches for \"\(query)\" in \(app.localizedName ?? "Unknown") (\(elements.count))")
    for el in elements {
        print(formatRefElement(el))
    }
}

func isRefToken(_ s: String) -> Bool {
    guard s.hasPrefix("e"), s.count >= 2 else { return false }
    return s.dropFirst().allSatisfy { $0.isNumber }
}

func cmdPress(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--app"])
    guard !parsed.positionals.isEmpty else {
        fail("Usage: macos-ctl press <ref|query> [--app <name>]")
    }
    let target = parsed.positionals.joined(separator: " ")

    if isRefToken(target) {
        guard let refs = loadRefsFile(), let stored = refs.elements[target] else {
            fail("Ref '\(target)' not found in \(REFS_FILE_PATH). Run 'tree' or 'find' first.")
        }
        checkRiskGuard(action: "press", targetLabel: stored.title)
        if let el = axElementByPath(pid: refs.pid, path: stored.path) {
            let err = AXUIElementPerformAction(el, kAXPressAction as CFString)
            if err == .success {
                print("Pressed [\(target)] \(stored.role) \"\(stored.title)\" via AXPress.")
                return
            }
        }
        // Fallback to CGEvent click at saved center point
        if let app = NSRunningApplication(processIdentifier: refs.pid) {
            activateApp(app)
            usleep(80_000)
        }
        performClick(x: Double(stored.x), y: Double(stored.y), rightButton: false, clickCount: 1)
        print("Pressed [\(target)] \(stored.role) \"\(stored.title)\" via CGEvent fallback click @(\(stored.x), \(stored.y)).")
        return
    }

    // Not a ref token -> check target query before searching, and also check resolved element label
    checkRiskGuard(action: "press", targetLabel: target)
    let app = resolveTargetApp(appName: parsed.option("--app"))
    let matches = collectAXElements(
        app: app,
        maxDepth: 6,
        interactiveOnly: false,
        limit: 10,
        query: target
    )
    guard let first = matches.first else {
        fail("No element matching '\(target)' found in \(app.localizedName ?? "Unknown").")
    }
    checkRiskGuard(action: "press", targetLabel: first.title)
    saveRefsFile(app: app, elements: matches)

    if let el = axElementByPath(pid: app.processIdentifier, path: first.path) {
        let err = AXUIElementPerformAction(el, kAXPressAction as CFString)
        if err == .success {
            print("Pressed \"\(first.title)\" (\(first.role)) via AXPress.")
            return
        }
    }
    activateApp(app)
    usleep(80_000)
    performClick(x: Double(first.x), y: Double(first.y), rightButton: false, clickCount: 1)
    print("Pressed \"\(first.title)\" (\(first.role)) via CGEvent fallback click @(\(first.x), \(first.y)).")
}

func cmdSet(_ args: [String]) {
    requireAccessibility()
    let parsed = parseArgs(args, valueOptions: ["--app"])
    guard parsed.positionals.count >= 2 else {
        fail("Usage: macos-ctl set <ref> <text> [--submit]")
    }
    let ref = parsed.positionals[0]
    let text = parsed.positionals[1...].joined(separator: " ")
    let submit = parsed.hasFlag("--submit")

    let pid: pid_t
    let path: [Int]
    let cx: Int
    let cy: Int
    let label: String

    if isRefToken(ref) {
        guard let refs = loadRefsFile(), let stored = refs.elements[ref] else {
            fail("Ref '\(ref)' not found in \(REFS_FILE_PATH). Run 'tree' or 'find' first.")
        }
        pid = refs.pid
        path = stored.path
        cx = stored.x
        cy = stored.y
        label = stored.title
    } else {
        let app = resolveTargetApp(appName: parsed.option("--app"))
        let matches = collectAXElements(app: app, maxDepth: 6, interactiveOnly: false, limit: 10, query: ref)
        guard let first = matches.first else {
            fail("No element matching '\(ref)' found.")
        }
        saveRefsFile(app: app, elements: matches)
        pid = app.processIdentifier
        path = first.path
        cx = first.x
        cy = first.y
        label = first.title
    }

    var usedFallback = false
    if let el = axElementByPath(pid: pid, path: path) {
        _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
        let readback = axStringAttribute(el, kAXValueAttribute)
        if err != .success || (readback != nil && readback != text) {
            usedFallback = true
        }
    } else {
        usedFallback = true
    }

    if usedFallback {
        if let app = NSRunningApplication(processIdentifier: pid) {
            activateApp(app)
            usleep(80_000)
        }
        performClick(x: Double(cx), y: Double(cy), rightButton: false, clickCount: 1)
        usleep(60_000)
        // Cmd+A to select existing text
        sendKeyCombo("cmd+a")
        usleep(50_000)
        typeUnicodeString(text, delayMs: 8)
    }

    if submit {
        usleep(50_000)
        sendKeyCombo("return")
    }

    let method = usedFallback ? "CGEvent fallback" : "AXValue"
    print("Set [\(ref)] \"\(label)\" via \(method)\(submit ? " + Return" : "").")
}

func cmdMenu(_ args: [String]) {
    requireAccessibility()
    guard args.count >= 2 else {
        fail("Usage: macos-ctl menu <app> <item1> [item2 ...]")
    }
    let appName = args[0]
    let menuPath = Array(args[1...])
    checkRiskGuard(action: "menu", targetLabel: menuPath.joined(separator: " -> "))
    for item in menuPath {
        checkRiskGuard(action: "menu", targetLabel: item)
    }
    let app = resolveTargetApp(appName: appName)

    activateApp(app)
    usleep(80_000)

    let appEl = AXUIElementCreateApplication(app.processIdentifier)
    guard let menuBarObj = axCopyAttribute(appEl, kAXMenuBarAttribute) else {
        fail("Could not access menu bar for '\(app.localizedName ?? appName)'.")
    }
    var currentContainer = (menuBarObj as! AXUIElement)

    for (stepIdx, targetName) in menuPath.enumerated() {
        let targetLower = targetName.lowercased()
        var candidates = axChildren(currentContainer)

        // If container has a single AXMenu child (e.g., AXMenuBarItem -> AXMenu), descend into it
        if candidates.count == 1 && axStringAttribute(candidates[0], kAXRoleAttribute) == "AXMenu" {
            candidates = axChildren(candidates[0])
        }

        // Prefer exact match, then case-insensitive substring match
        let matched = candidates.first(where: {
            (axStringAttribute($0, kAXTitleAttribute)?.lowercased() == targetLower)
        }) ?? candidates.first(where: {
            (axStringAttribute($0, kAXTitleAttribute)?.lowercased().contains(targetLower) ?? false)
        })

        guard let item = matched else {
            let available = candidates.compactMap { axStringAttribute($0, kAXTitleAttribute) }.filter { !$0.isEmpty }
            fail("Menu item '\(targetName)' not found at step \(stepIdx + 1). Available: \(available.joined(separator: ", "))")
        }

        let err = AXUIElementPerformAction(item, kAXPressAction as CFString)
        if err != .success && stepIdx == menuPath.count - 1 {
            fail("Failed to press menu item '\(targetName)' (AXError \(err.rawValue)).")
        }
        usleep(60_000)
        currentContainer = item
    }

    print("Triggered menu: \(app.localizedName ?? appName) -> \(menuPath.joined(separator: " -> "))")
}

// MARK: - 9. Low-level CGEvent Mouse & Keyboard

func performMoveMouse(x: Double, y: Double) {
    let pt = CGPoint(x: x, y: y)
    if let ev = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left) {
        ev.post(tap: .cghidEventTap)
    }
}

func performClick(x: Double, y: Double, rightButton: Bool, clickCount: Int) {
    let pt = CGPoint(x: x, y: y)
    let downType: CGEventType = rightButton ? .rightMouseDown : .leftMouseDown
    let upType: CGEventType = rightButton ? .rightMouseUp : .leftMouseUp
    let button: CGMouseButton = rightButton ? .right : .left

    performMoveMouse(x: x, y: y)
    usleep(15_000)

    for i in 1...max(1, clickCount) {
        if let down = CGEvent(mouseEventSource: nil, mouseType: downType, mouseCursorPosition: pt, mouseButton: button) {
            down.setIntegerValueField(.mouseEventClickState, value: Int64(i))
            down.post(tap: .cghidEventTap)
        }
        usleep(20_000)
        if let up = CGEvent(mouseEventSource: nil, mouseType: upType, mouseCursorPosition: pt, mouseButton: button) {
            up.setIntegerValueField(.mouseEventClickState, value: Int64(i))
            up.post(tap: .cghidEventTap)
        }
        if i < clickCount {
            usleep(40_000)
        }
    }
}

func cmdClick(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--count"])
    guard parsed.positionals.count >= 2,
          let x = Double(parsed.positionals[0]),
          let y = Double(parsed.positionals[1]) else {
        fail("Usage: macos-ctl click <x> <y> [--right] [--double] [--count <N>]")
    }
    let right = parsed.hasFlag("--right")
    var count = Int(parsed.option("--count") ?? "1") ?? 1
    if parsed.hasFlag("--double") { count = max(count, 2) }

    performClick(x: x, y: y, rightButton: right, clickCount: count)
    print("Clicked @(\(Int(x)), \(Int(y))) \(right ? "right" : "left") x\(count).")
}

func cmdMoveMouse(_ args: [String]) {
    guard args.count >= 2, let x = Double(args[0]), let y = Double(args[1]) else {
        fail("Usage: macos-ctl move-mouse <x> <y>")
    }
    performMoveMouse(x: x, y: y)
    print("Moved mouse to (\(Int(x)), \(Int(y))).")
}

func cmdDrag(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--duration"])
    guard parsed.positionals.count >= 4,
          let x1 = Double(parsed.positionals[0]),
          let y1 = Double(parsed.positionals[1]),
          let x2 = Double(parsed.positionals[2]),
          let y2 = Double(parsed.positionals[3]) else {
        fail("Usage: macos-ctl drag <x1> <y1> <x2> <y2> [--duration <sec>]")
    }
    let duration = max(Double(parsed.option("--duration") ?? "0.3") ?? 0.3, 0.05)
    let steps = max(10, Int(duration * 60))
    let stepUs = UInt32((duration / Double(steps)) * 1_000_000)

    let startPt = CGPoint(x: x1, y: y1)
    let endPt = CGPoint(x: x2, y: y2)

    performMoveMouse(x: x1, y: y1)
    usleep(20_000)

    if let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: startPt, mouseButton: .left) {
        down.post(tap: .cghidEventTap)
    }
    usleep(20_000)

    for i in 1...steps {
        let t = Double(i) / Double(steps)
        let cx = x1 + (x2 - x1) * t
        let cy = y1 + (y2 - y1) * t
        if let drag = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDragged, mouseCursorPosition: CGPoint(x: cx, y: cy), mouseButton: .left) {
            drag.post(tap: .cghidEventTap)
        }
        usleep(stepUs)
    }

    if let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: endPt, mouseButton: .left) {
        up.post(tap: .cghidEventTap)
    }
    print("Dragged from (\(Int(x1)), \(Int(y1))) to (\(Int(x2)), \(Int(y2))) over \(duration)s.")
}

func cmdScroll(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--x", "--y"])
    guard parsed.positionals.count >= 2,
          let dx = Int32(parsed.positionals[0]),
          let dy = Int32(parsed.positionals[1]) else {
        fail("Usage: macos-ctl scroll <dx> <dy> [--x <x> --y <y>]")
    }
    if let xs = parsed.option("--x"), let ys = parsed.option("--y"),
       let x = Double(xs), let y = Double(ys) {
        performMoveMouse(x: x, y: y)
        usleep(20_000)
    }
    if let ev = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0) {
        ev.post(tap: .cghidEventTap)
    }
    print("Scrolled dx=\(dx), dy=\(dy).")
}

func postKeyEvent(keyCode: CGKeyCode, flags: CGEventFlags = []) {
    if let down = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: true) {
        down.flags = flags
        down.post(tap: .cghidEventTap)
    }
    usleep(12_000)
    if let up = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: false) {
        up.flags = flags
        up.post(tap: .cghidEventTap)
    }
}

func typeUnicodeString(_ text: String, delayMs: Int) {
    let delayUs = UInt32(max(0, delayMs) * 1000)
    for ch in text {
        if ch == "\n" || ch == "\r" {
            postKeyEvent(keyCode: 36)
        } else if ch == "\t" {
            postKeyEvent(keyCode: 48)
        } else {
            let utf16 = Array(String(ch).utf16)
            if let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true) {
                down.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: utf16)
                down.post(tap: .cghidEventTap)
            }
            if let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) {
                up.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: utf16)
                up.post(tap: .cghidEventTap)
            }
        }
        if delayUs > 0 {
            usleep(delayUs)
        }
    }
}

func cmdType(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--delay-ms"])
    guard !parsed.positionals.isEmpty else {
        fail("Usage: macos-ctl type <text> [--delay-ms <N>]")
    }
    let text = parsed.positionals.joined(separator: " ")
    let delayMs = Int(parsed.option("--delay-ms") ?? "8") ?? 8
    typeUnicodeString(text, delayMs: delayMs)
    print("Typed \(text.count) characters.")
}

let KEY_CODE_MAP: [String: CGKeyCode] = [
    // Letters (US ANSI virtual key codes)
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    // Numbers
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    // Punctuation
    "=": 24, "-": 27, "]": 30, "[": 33, "'": 39, ";": 41, "\\": 42, ",": 43, "/": 44, ".": 47, "`": 50,
    // Special keys
    "return": 36, "enter": 36,
    "tab": 48,
    "space": 49,
    "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53,
    "forwarddelete": 117,
    "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    // Function keys
    "f1": 122, "f2": 120, "f3": 99, "f4": 118,
    "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111
]

func sendKeyCombo(_ combo: String) {
    let parts = combo.lowercased().split(separator: "+").map { String($0).trimmingCharacters(in: .whitespaces) }
    guard !parts.isEmpty else {
        fail("Invalid key combo '\(combo)'.")
    }

    var flags: CGEventFlags = []
    var keyToken: String? = nil

    for part in parts {
        switch part {
        case "cmd", "command", "meta", "super":
            flags.insert(.maskCommand)
        case "shift":
            flags.insert(.maskShift)
        case "alt", "opt", "option":
            flags.insert(.maskAlternate)
        case "ctrl", "control":
            flags.insert(.maskControl)
        default:
            if keyToken != nil {
                fail("Multiple non-modifier keys in combo '\(combo)'.")
            }
            keyToken = part
        }
    }

    guard let k = keyToken, let code = KEY_CODE_MAP[k] else {
        fail("Unknown key '\(keyToken ?? "")' in combo '\(combo)'.")
    }

    postKeyEvent(keyCode: code, flags: flags)
}

func cmdKey(_ args: [String]) {
    guard !args.isEmpty else {
        fail("Usage: macos-ctl key <combo>  (e.g. cmd+shift+s, enter, f5)")
    }
    for combo in args {
        sendKeyCombo(combo)
        usleep(25_000)
    }
    print("Sent key: \(args.joined(separator: " "))")
}

// MARK: - 10. System & Screenshot

func cmdClipboard(_ args: [String]) {
    guard let sub = args.first?.lowercased() else {
        fail("Usage: macos-ctl clipboard <get|set> [text]")
    }
    let pb = NSPasteboard.general
    switch sub {
    case "get":
        print(pb.string(forType: .string) ?? "")
    case "set":
        let text = args.dropFirst().joined(separator: " ")
        pb.clearContents()
        pb.setString(text, forType: .string)
        print("Clipboard updated (\(text.count) chars).")
    default:
        fail("Unknown clipboard subcommand '\(sub)'. Use 'get' or 'set'.")
    }
}

func escapeAppleScriptString(_ s: String) -> String {
    return s
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
}

func cmdNotify(_ args: [String]) {
    guard args.count >= 2 else {
        fail("Usage: macos-ctl notify <title> <message>")
    }
    let title = escapeAppleScriptString(args[0])
    let message = escapeAppleScriptString(args[1...].joined(separator: " "))
    let script = "display notification \"\(message)\" with title \"\(title)\""
    let res = runProcess("/usr/bin/osascript", args: ["-e", script])
    if res.status != 0 {
        fail("Notification failed: \(res.stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
    }
    print("Notification sent.")
}

func cmdShortcut(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--input"])
    guard !parsed.positionals.isEmpty else {
        fail("Usage: macos-ctl shortcut <name> [--input <text>]")
    }
    let name = parsed.positionals.joined(separator: " ")
    let stdinData = parsed.option("--input")?.data(using: .utf8)
    let res = runProcess("/usr/bin/shortcuts", args: ["run", name], stdinData: stdinData)
    if res.status != 0 {
        fail("Shortcut '\(name)' failed: \(res.stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
    }
    if !res.stdout.isEmpty {
        print(res.stdout, terminator: "")
    } else {
        print("Shortcut '\(name)' completed.")
    }
}

func drawCoordinateGrid(onImageAt path: String, originX: Double, originY: Double, logicalWidth: Double?) {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        fail("Failed to load screenshot image at '\(path)' for grid overlay.")
    }

    let pxW = cgImage.width
    let pxH = cgImage.height

    let scale: CGFloat
    if let lw = logicalWidth, lw > 0 {
        scale = CGFloat(pxW) / CGFloat(lw)
    } else if let mainScreen = NSScreen.main, mainScreen.frame.width > 0 {
        let inferred = CGFloat(pxW) / mainScreen.frame.width
        scale = inferred > 0.5 ? inferred : mainScreen.backingScaleFactor
    } else {
        scale = 2.0
    }

    let colorSpace = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(
        data: nil,
        width: pxW,
        height: pxH,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        fail("Failed to create CGContext for grid overlay.")
    }

    // Draw original image (unflipped)
    ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: pxW, height: pxH))

    // Flip coordinate system to top-left logical points
    ctx.saveGState()
    ctx.translateBy(x: 0, y: CGFloat(pxH))
    ctx.scaleBy(x: scale, y: -scale)

    let logW = Double(pxW) / Double(scale)
    let logH = Double(pxH) / Double(scale)
    let step = 100.0

    let firstX = ceil(originX / step) * step
    let firstY = ceil(originY / step) * step

    // Draw semi-transparent grid lines
    ctx.setStrokeColor(CGColor(red: 0.0, green: 0.85, blue: 1.0, alpha: 0.42))
    ctx.setLineWidth(1.0 / scale)

    var gx = firstX
    while gx <= originX + logW {
        let localX = CGFloat(gx - originX)
        ctx.move(to: CGPoint(x: localX, y: 0))
        ctx.addLine(to: CGPoint(x: localX, y: CGFloat(logH)))
        gx += step
    }

    var gy = firstY
    while gy <= originY + logH {
        let localY = CGFloat(gy - originY)
        ctx.move(to: CGPoint(x: 0, y: localY))
        ctx.addLine(to: CGPoint(x: CGFloat(logW), y: localY))
        gy += step
    }
    ctx.strokePath()

    // Draw coordinate labels at intersections (every 200pt or 100pt)
    let nsCtx = NSGraphicsContext(cgContext: ctx, flipped: true)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = nsCtx

    let font = NSFont.monospacedSystemFont(ofSize: 10, weight: .semibold)
    let textAttrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: NSColor(calibratedRed: 1.0, green: 1.0, blue: 1.0, alpha: 0.95)
    ]

    gy = firstY
    while gy <= originY + logH - 12 {
        gx = firstX
        while gx <= originX + logW - 36 {
            // Label every 200pt in x or every 100pt on small images
            let skipDense = (logW > 700 && Int(gx) % 200 != 0 && Int(gy) % 200 != 0)
            if !skipDense {
                let localX = CGFloat(gx - originX)
                let localY = CGFloat(gy - originY)
                let label = "\(Int(gx)),\(Int(gy))" as NSString
                let sz = label.size(withAttributes: textAttrs)
                let bgRect = CGRect(x: localX + 2, y: localY + 2, width: sz.width + 6, height: sz.height + 2)
                ctx.setFillColor(CGColor(red: 0.0, green: 0.0, blue: 0.0, alpha: 0.62))
                ctx.fill(bgRect)
                label.draw(at: CGPoint(x: localX + 5, y: localY + 3), withAttributes: textAttrs)
            }
            gx += step
        }
        gy += step
    }

    NSGraphicsContext.restoreGraphicsState()
    ctx.restoreGState()

    guard let outImage = ctx.makeImage(),
          let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        fail("Failed to encode grid-overlaid PNG to '\(path)'.")
    }
    CGImageDestinationAddImage(dest, outImage, nil)
    if !CGImageDestinationFinalize(dest) {
        fail("Failed to write grid-overlaid PNG to '\(path)'.")
    }
}

func somColorForRole(_ role: String) -> (CGFloat, CGFloat, CGFloat) {
    switch role {
    case "AXButton", "AXPopUpButton", "AXMenuButton", "AXTabButton", "AXTab":
        return (0.94, 0.22, 0.22) // Red/Coral
    case "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox":
        return (0.12, 0.50, 0.96) // Blue
    case "AXCheckBox", "AXRadioButton", "AXSlider":
        return (0.10, 0.70, 0.35) // Green
    case "AXLink", "AXMenuItem":
        return (0.60, 0.22, 0.88) // Purple
    default:
        return (0.92, 0.50, 0.08) // Orange
    }
}

func drawSoMAnnotations(
    onImageAt path: String,
    originX: Double,
    originY: Double,
    logicalWidth: Double?,
    elements: [RefElement]
) -> [RefElement] {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        fail("Failed to load screenshot image at '\(path)' for SoM annotation.")
    }

    let pxW = cgImage.width
    let pxH = cgImage.height

    let scale: CGFloat
    if let lw = logicalWidth, lw > 0 {
        scale = CGFloat(pxW) / CGFloat(lw)
    } else if let mainScreen = NSScreen.main, mainScreen.frame.width > 0 {
        let inferred = CGFloat(pxW) / mainScreen.frame.width
        scale = inferred > 0.5 ? inferred : mainScreen.backingScaleFactor
    } else {
        scale = 2.0
    }

    let logW = Double(pxW) / Double(scale)
    let logH = Double(pxH) / Double(scale)
    let captureRect = CGRect(x: originX, y: originY, width: logW, height: logH)

    // Filter to visible elements inside the captured bounds and renumber sequentially e1..eN
    var visibleElements: [RefElement] = []
    for el in elements {
        guard el.w > 0 && el.h > 0 else { continue }
        let elLeft = Double(el.x) - Double(el.w) / 2.0
        let elTop = Double(el.y) - Double(el.h) / 2.0
        let elRect = CGRect(x: elLeft, y: elTop, width: Double(el.w), height: Double(el.h))
        let clipped = elRect.intersection(captureRect)
        if clipped.isNull || clipped.width < 2 || clipped.height < 2 { continue }

        let refId = "e\(visibleElements.count + 1)"
        visibleElements.append(RefElement(
            ref: refId,
            role: el.role,
            title: el.title,
            description: el.description,
            value: el.value,
            identifier: el.identifier,
            x: el.x,
            y: el.y,
            w: el.w,
            h: el.h,
            path: el.path
        ))
    }

    let colorSpace = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(
        data: nil,
        width: pxW,
        height: pxH,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        fail("Failed to create CGContext for SoM annotation.")
    }

    // Draw base image (unflipped)
    ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: pxW, height: pxH))

    // Flip to top-left logical coordinates
    ctx.saveGState()
    ctx.translateBy(x: 0, y: CGFloat(pxH))
    ctx.scaleBy(x: scale, y: -scale)

    let nsCtx = NSGraphicsContext(cgContext: ctx, flipped: true)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = nsCtx

    let font = NSFont.monospacedSystemFont(ofSize: 10, weight: .bold)
    let textAttrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: NSColor.white
    ]

    for el in visibleElements {
        let elLeft = Double(el.x) - Double(el.w) / 2.0
        let elTop = Double(el.y) - Double(el.h) / 2.0
        let elRect = CGRect(x: elLeft, y: elTop, width: Double(el.w), height: Double(el.h))
        let clipped = elRect.intersection(captureRect)
        guard !clipped.isNull else { continue }

        let localRect = CGRect(
            x: CGFloat(clipped.origin.x - originX),
            y: CGFloat(clipped.origin.y - originY),
            width: CGFloat(clipped.size.width),
            height: CGFloat(clipped.size.height)
        )

        let (r, g, b) = somColorForRole(el.role)
        let strokeColor = CGColor(red: r, green: g, blue: b, alpha: 0.92)
        let fillTint = CGColor(red: r, green: g, blue: b, alpha: 0.08)

        // Subtle tint + 2pt bounding box
        ctx.setFillColor(fillTint)
        ctx.fill(localRect)
        ctx.setStrokeColor(strokeColor)
        ctx.setLineWidth(2.0)
        ctx.stroke(localRect)

        // High-contrast label badge in upper-left corner of visible rect
        let tag = el.ref as NSString
        let tagSize = tag.size(withAttributes: textAttrs)
        let padX: CGFloat = 3.0
        let padY: CGFloat = 1.0
        let badgeW = tagSize.width + padX * 2
        let badgeH = tagSize.height + padY * 2

        let badgeX = min(max(0, localRect.origin.x), max(0, CGFloat(logW) - badgeW))
        let badgeY = min(max(0, localRect.origin.y), max(0, CGFloat(logH) - badgeH))
        let badgeRect = CGRect(x: badgeX, y: badgeY, width: badgeW, height: badgeH)

        ctx.setFillColor(CGColor(red: r, green: g, blue: b, alpha: 0.96))
        ctx.fill(badgeRect)
        tag.draw(at: CGPoint(x: badgeX + padX, y: badgeY + padY), withAttributes: textAttrs)
    }

    NSGraphicsContext.restoreGraphicsState()
    ctx.restoreGState()

    guard let outImage = ctx.makeImage(),
          let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        fail("Failed to encode SoM-annotated PNG to '\(path)'.")
    }
    CGImageDestinationAddImage(dest, outImage, nil)
    if !CGImageDestinationFinalize(dest) {
        fail("Failed to write SoM-annotated PNG to '\(path)'.")
    }

    return visibleElements
}

func cmdScreenshot(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--app", "--region"])
    guard let outPathRaw = parsed.positionals.first else {
        fail("Usage: macos-ctl screenshot <output.png> [--app <name>] [--region x,y,w,h] [--grid] [--annotate]")
    }

    let withGrid = parsed.hasFlag("--grid")
    let withAnnotate = parsed.hasFlag("--annotate")

    if withAnnotate {
        requireAccessibility()
    }

    if !CGPreflightScreenCaptureAccess() {
        fail("""
        Screen Recording permission is not granted.
        Enable Screen Recording for your terminal/runner, then retry:
          open "\(SCREEN_RECORDING_PREFS_URL)"
        """)
    }

    let outPath = NSString(string: outPathRaw).expandingTildeInPath
    var scArgs: [String] = ["-x"]
    var originX = 0.0
    var originY = 0.0
    var logicalW: Double? = nil

    let appOpt = parsed.option("--app")
    if let appName = appOpt {
        let wins = listWindows(appFilter: appName)
        guard let win = wins.first else {
            fail("No visible window found for app '\(appName)'.")
        }
        if withAnnotate {
            // Omit window drop shadow so window coordinates map 1:1 to image pixels
            scArgs.append("-o")
        }
        scArgs.append(contentsOf: ["-l", "\(win.windowId)"])
        originX = win.x
        originY = win.y
        logicalW = win.width
    } else if let regionStr = parsed.option("--region") {
        let comps = regionStr.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        guard comps.count == 4 else {
            fail("Invalid --region format. Expected x,y,w,h (e.g. 100,100,800,600).")
        }
        scArgs.append(contentsOf: ["-R", regionStr])
        originX = comps[0]
        originY = comps[1]
        logicalW = comps[2]
    }

    scArgs.append(outPath)

    let res = runProcess("/usr/sbin/screencapture", args: scArgs)
    if res.status != 0 {
        fail("screencapture failed: \(res.stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
    }

    // Draw grid first if requested, then SoM boxes on top
    if withGrid {
        drawCoordinateGrid(onImageAt: outPath, originX: originX, originY: originY, logicalWidth: logicalW)
    }

    var annotatedElements: [RefElement] = []
    if withAnnotate {
        let targetApp = resolveTargetApp(appName: appOpt)
        let rawElements = collectAXElements(
            app: targetApp,
            maxDepth: 8,
            interactiveOnly: true,
            limit: 150
        )
        annotatedElements = drawSoMAnnotations(
            onImageAt: outPath,
            originX: originX,
            originY: originY,
            logicalWidth: logicalW,
            elements: rawElements
        )
        saveRefsFile(app: targetApp, elements: annotatedElements)
    }

    var tags: [String] = []
    if withGrid { tags.append("coordinate grid") }
    if withAnnotate { tags.append("SoM: \(annotatedElements.count) refs") }
    let suffix = tags.isEmpty ? "" : " (\(tags.joined(separator: ", ")))"
    print("Saved screenshot: \(outPath)\(suffix)")

    if withAnnotate {
        for el in annotatedElements {
            let labelPart = el.title.isEmpty ? "" : " \"\(el.title)\""
            print("[\(el.ref)] \(el.role)\(labelPart) @(\(el.x), \(el.y))")
        }
    }
}

// MARK: - 11. Self-Learning Recipes (Record & Recipe)

struct RecipeStep: Codable {
    var action: String?
    var type: String?
    var app: String?
    var target: String?
    var ref: String?
    var role: String?
    var title: String?
    var text: String?
    var value: String?
    var items: [String]?
    var path: [String]?
    var combo: String?
    var key: String?
    var layout: String?
    var display: Int?
    var seconds: Double?
    var duration: Double?
    var x: Double?
    var y: Double?
    var submit: Bool?
    var delay_ms: Int?
    var force: Bool?

    var resolvedAction: String {
        return (action ?? type ?? "").lowercased()
    }
}

struct Recipe: Codable {
    var name: String?
    var description: String?
    var params: [String]?
    var steps: [RecipeStep]
}

struct RecipeSummary: Codable {
    let name: String
    let description: String
    let params: [String]
    let step_count: Int
    let path: String
}

func recipesDirectoryURL() -> URL {
    return FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".config", isDirectory: true)
        .appendingPathComponent("antigravity-macos", isDirectory: true)
        .appendingPathComponent("recipes", isDirectory: true)
}

func ensureRecipesDirectory() -> URL {
    let dir = recipesDirectoryURL()
    do {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    } catch {
        fail("Failed to create recipes directory at '\(dir.path)': \(error.localizedDescription)")
    }
    return dir
}

func validatedRecipeName(_ raw: String) -> String {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty,
          !trimmed.contains("/"),
          !trimmed.contains("\\"),
          !trimmed.contains(".."),
          trimmed != "." else {
        fail("Invalid recipe name '\(raw)'. Use a simple name without path separators.")
    }
    if trimmed.hasSuffix(".json") {
        return String(trimmed.dropLast(5))
    }
    return trimmed
}

func recipeFileURL(named raw: String) -> URL {
    let name = validatedRecipeName(raw)
    return recipesDirectoryURL().appendingPathComponent("\(name).json")
}

// Reverse lookup table for key codes used during recording
let REVERSE_KEY_CODE_MAP: [CGKeyCode: String] = {
    var map: [CGKeyCode: String] = [:]
    let preferred: [(String, CGKeyCode)] = [
        ("return", 36), ("tab", 48), ("space", 49), ("delete", 51), ("escape", 53),
        ("forwarddelete", 117), ("home", 115), ("end", 119), ("pageup", 116), ("pagedown", 121),
        ("left", 123), ("right", 124), ("down", 125), ("up", 126)
    ]
    for (name, code) in KEY_CODE_MAP {
        if map[code] == nil { map[code] = name }
    }
    for (name, code) in preferred {
        map[code] = name
    }
    return map
}()

class RecorderState {
    let appFilter: String?
    var steps: [RecipeStep] = []
    var pendingText: String = ""
    var tap: CFMachPort? = nil

    init(appFilter: String?) {
        self.appFilter = appFilter?.lowercased()
    }

    func flushPendingText() {
        guard !pendingText.isEmpty else { return }
        steps.append(RecipeStep(
            action: "type",
            type: "type",
            text: pendingText
        ))
        pendingText = ""
    }

    func matchesAppFilter(_ appName: String?) -> Bool {
        guard let filter = appFilter, !filter.isEmpty else { return true }
        return (appName ?? "").lowercased().contains(filter)
    }

    func handleMouseDown(_ event: CGEvent) {
        let loc = event.location
        let sysWide = AXUIElementCreateSystemWide()
        var hitObj: AXUIElement?
        var appName: String = NSWorkspace.shared.frontmostApplication?.localizedName ?? "Unknown"
        var role: String = "AXUnknown"
        var title: String = ""

        if AXUIElementCopyElementAtPosition(sysWide, Float(loc.x), Float(loc.y), &hitObj) == .success,
           let el = hitObj {
            var pid: pid_t = 0
            if AXUIElementGetPid(el, &pid) == .success,
               let running = NSRunningApplication(processIdentifier: pid),
               let name = running.localizedName {
                appName = name
            }
            role = axStringAttribute(el, kAXRoleAttribute) ?? "AXUnknown"
            let t = cleanLabel(axStringAttribute(el, kAXTitleAttribute))
            let d = cleanLabel(axStringAttribute(el, kAXDescriptionAttribute))
            let v = cleanLabel(axStringAttribute(el, kAXValueAttribute))
            let id = cleanLabel(axStringAttribute(el, kAXIdentifierAttribute))
            title = !t.isEmpty ? t : (!d.isEmpty ? d : (!v.isEmpty ? v : id))
        }

        guard matchesAppFilter(appName) else { return }
        flushPendingText()
        steps.append(RecipeStep(
            action: "press",
            type: "press",
            app: appName,
            role: role,
            title: title,
            x: Double(Int(round(loc.x))),
            y: Double(Int(round(loc.y)))
        ))
    }

    func handleKeyDown(_ event: CGEvent) {
        let frontName = NSWorkspace.shared.frontmostApplication?.localizedName
        guard matchesAppFilter(frontName) else { return }

        let keyCode = CGKeyCode(event.getIntegerValueField(.keyboardEventKeycode))
        let flags = event.flags
        let hasCmd = flags.contains(.maskCommand)
        let hasCtrl = flags.contains(.maskControl)
        let hasAlt = flags.contains(.maskAlternate)
        let hasShift = flags.contains(.maskShift)

        // Shortcut with Cmd or Ctrl -> record as key combo
        if hasCmd || hasCtrl {
            flushPendingText()
            var parts: [String] = []
            if hasCmd { parts.append("cmd") }
            if hasCtrl { parts.append("ctrl") }
            if hasAlt { parts.append("alt") }
            if hasShift { parts.append("shift") }
            if let keyName = REVERSE_KEY_CODE_MAP[keyCode] {
                parts.append(keyName)
                steps.append(RecipeStep(
                    action: "key",
                    type: "key",
                    combo: parts.joined(separator: "+")
                ))
            }
            return
        }

        // Special navigation/control keys
        let specialCodes: Set<CGKeyCode> = [36, 48, 51, 53, 115, 116, 117, 119, 121, 123, 124, 125, 126]
        if specialCodes.contains(keyCode) {
            if keyCode == 51 && !pendingText.isEmpty {
                // Backspace edits the current typed buffer cleanly
                pendingText.removeLast()
                return
            }
            flushPendingText()
            if let keyName = REVERSE_KEY_CODE_MAP[keyCode] {
                steps.append(RecipeStep(
                    action: "key",
                    type: "key",
                    combo: keyName
                ))
            }
            return
        }

        // Extract typed Unicode character(s)
        var actualLen: Int = 0
        var chars = [UniChar](repeating: 0, count: 8)
        event.keyboardGetUnicodeString(maxStringLength: 8, actualStringLength: &actualLen, unicodeString: &chars)
        if actualLen > 0 {
            let s = String(utf16CodeUnits: chars, count: actualLen)
            let filtered = s.filter { ch in
                guard let scalar = ch.unicodeScalars.first else { return false }
                return scalar.value >= 0x20 && scalar.value != 0x7F
            }
            if !filtered.isEmpty {
                pendingText.append(filtered)
            }
        }
    }
}

func cmdRecord(_ args: [String]) {
    let parsed = parseArgs(args, valueOptions: ["--duration", "--output", "--app"])
    let rawDur = Int(parsed.option("--duration") ?? "15") ?? 15
    let duration = min(max(rawDur, 1), 120)
    let outputPath = parsed.option("--output").map { NSString(string: $0).expandingTildeInPath }
    let appFilter = parsed.option("--app")

    // Always preflight Input Monitoring permission first so we never hang
    if !CGPreflightListenEventAccess() {
        fail("""
        Input Monitoring permission is not granted.
        Grant permission to your terminal/runner in System Settings, then retry:
          open "\(INPUT_MONITORING_PREFS_URL)"
        """)
    }

    let state = RecorderState(appFilter: appFilter)
    let eventMask: CGEventMask =
        (CGEventMask(1) << CGEventType.leftMouseDown.rawValue) |
        (CGEventMask(1) << CGEventType.keyDown.rawValue)

    let callback: CGEventTapCallBack = { _, type, event, refcon in
        guard let refcon = refcon else {
            return Unmanaged.passUnretained(event)
        }
        let rec = Unmanaged<RecorderState>.fromOpaque(refcon).takeUnretainedValue()
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap = rec.tap {
                CGEvent.tapEnable(tap: tap, enable: true)
            }
            return Unmanaged.passUnretained(event)
        }
        if type == .leftMouseDown {
            rec.handleMouseDown(event)
        } else if type == .keyDown {
            rec.handleKeyDown(event)
        }
        return Unmanaged.passUnretained(event)
    }

    guard let tap = CGEvent.tapCreate(
        tap: .cgSessionEventTap,
        place: .headInsertEventTap,
        options: .listenOnly,
        eventsOfInterest: eventMask,
        callback: callback,
        userInfo: Unmanaged.passUnretained(state).toOpaque()
    ) else {
        fail("""
        Failed to create CGEvent tap. Ensure Input Monitoring and Accessibility permissions are granted:
          open "\(INPUT_MONITORING_PREFS_URL)"
        """)
    }
    state.tap = tap

    let runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
    let mainRunLoop = CFRunLoopGetMain()
    CFRunLoopAddSource(mainRunLoop, runLoopSource, .commonModes)
    CGEvent.tapEnable(tap: tap, enable: true)

    // Graceful stop on SIGINT
    signal(SIGINT, SIG_IGN)
    let sigSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    sigSource.setEventHandler {
        CFRunLoopStop(mainRunLoop)
    }
    sigSource.resume()

    // Duration timeout
    let timer = DispatchSource.makeTimerSource(queue: .main)
    timer.schedule(deadline: .now() + .seconds(duration))
    timer.setEventHandler {
        CFRunLoopStop(mainRunLoop)
    }
    timer.resume()

    let appNote = appFilter != nil ? " (filter: \(appFilter!))" : ""
    printErr("Recording for up to \(duration)s\(appNote)... Press Ctrl-C to stop early.")
    CFRunLoopRun()

    timer.cancel()
    sigSource.cancel()
    CGEvent.tapEnable(tap: tap, enable: false)
    CFRunLoopRemoveSource(mainRunLoop, runLoopSource, .commonModes)

    state.flushPendingText()

    let recipe = Recipe(
        name: "recorded",
        description: "Recorded via macos-ctl record (\(state.steps.count) steps)",
        params: [],
        steps: state.steps
    )

    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    guard let data = try? encoder.encode(recipe),
          let jsonStr = String(data: data, encoding: .utf8) else {
        fail("Failed to encode recorded recipe JSON.")
    }

    if let out = outputPath {
        let outURL = URL(fileURLWithPath: out)
        try? FileManager.default.createDirectory(at: outURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        do {
            try data.write(to: outURL)
            print("Saved recorded recipe (\(state.steps.count) steps) to: \(out)")
        } catch {
            fail("Failed to write recorded recipe to '\(out)': \(error.localizedDescription)")
        }
    } else {
        print(jsonStr)
    }
}

func extractPlaceholders(from text: String) -> [String] {
    guard let regex = try? NSRegularExpression(pattern: "\\{\\{([A-Za-z0-9_.-]+)\\}\\}", options: []) else {
        return []
    }
    let ns = text as NSString
    let matches = regex.matches(in: text, options: [], range: NSRange(location: 0, length: ns.length))
    return matches.map { ns.substring(with: $0.range(at: 1)) }
}

func substituteParams(in text: String?, params: [String: String]) -> String? {
    guard var result = text else { return nil }
    for (k, v) in params {
        result = result.replacingOccurrences(of: "{{\(k)}}", with: v)
    }
    return result
}

func collectPlaceholdersInStep(_ step: RecipeStep) -> [String] {
    var found: [String] = []
    let fields: [String?] = [
        step.app, step.target, step.ref, step.role, step.title,
        step.text, step.value, step.combo, step.key, step.layout
    ]
    for f in fields {
        if let s = f { found.append(contentsOf: extractPlaceholders(from: s)) }
    }
    for arr in [step.items, step.path] {
        if let list = arr {
            for s in list { found.append(contentsOf: extractPlaceholders(from: s)) }
        }
    }
    return found
}

func applyParamsToStep(_ step: RecipeStep, params: [String: String]) -> RecipeStep {
    var s = step
    s.app = substituteParams(in: s.app, params: params)
    s.target = substituteParams(in: s.target, params: params)
    s.ref = substituteParams(in: s.ref, params: params)
    s.role = substituteParams(in: s.role, params: params)
    s.title = substituteParams(in: s.title, params: params)
    s.text = substituteParams(in: s.text, params: params)
    s.value = substituteParams(in: s.value, params: params)
    s.combo = substituteParams(in: s.combo, params: params)
    s.key = substituteParams(in: s.key, params: params)
    s.layout = substituteParams(in: s.layout, params: params)
    if let items = s.items {
        s.items = items.map { substituteParams(in: $0, params: params) ?? $0 }
    }
    if let path = s.path {
        s.path = path.map { substituteParams(in: $0, params: params) ?? $0 }
    }
    return s
}

func describeRecipeStep(_ step: RecipeStep) -> String {
    let act = step.resolvedAction
    switch act {
    case "open":
        return "open \"\(step.target ?? step.app ?? "")\""
    case "focus":
        return "focus \"\(step.app ?? step.target ?? "")\""
    case "menu":
        let menuItems = step.items ?? step.path ?? []
        return "menu \"\(step.app ?? "")\" -> \(menuItems.joined(separator: " -> "))"
    case "press":
        let lbl = step.target ?? step.ref ?? step.title ?? ""
        let appPart = step.app != nil ? " [app=\(step.app!)]" : ""
        let coordPart = (step.x != nil && step.y != nil) ? " @(\(Int(step.x!)), \(Int(step.y!)))" : ""
        return "press \"\(lbl)\"\(appPart)\(coordPart)"
    case "set":
        let tgt = step.ref ?? step.target ?? ""
        let txt = step.text ?? step.value ?? ""
        let sub = (step.submit == true) ? " --submit" : ""
        return "set \"\(tgt)\" = \"\(txt)\"\(sub)"
    case "type":
        let txt = step.text ?? step.value ?? ""
        return "type \"\(txt)\""
    case "key":
        return "key \(step.combo ?? step.key ?? "")"
    case "wait":
        let sec = step.seconds ?? step.duration ?? 1.0
        return "wait \(sec)s"
    case "window-tile":
        let disp = step.display != nil ? " --display \(step.display!)" : ""
        return "window-tile \"\(step.app ?? step.target ?? "")\" \(step.layout ?? "")\(disp)"
    case "quit":
        let forcePart = (step.force == true) ? " --force" : ""
        return "quit \"\(step.app ?? step.target ?? "")\"\(forcePart)"
    default:
        return "\(act)"
    }
}

func recipeStepRiskInfo(_ step: RecipeStep) -> (action: String, target: String, keyword: String)? {
    let act = step.resolvedAction
    switch act {
    case "press":
        let lbl = [step.target, step.ref, step.title].compactMap { $0 }.joined(separator: " ")
        if let kw = evaluateRiskGuard(action: "press", targetLabel: lbl) {
            return ("press", lbl, kw)
        }
    case "menu":
        let items = step.items ?? step.path ?? []
        let joined = items.joined(separator: " -> ")
        if let kw = evaluateRiskGuard(action: "menu", targetLabel: joined) {
            return ("menu", joined, kw)
        }
        for item in items {
            if let kw = evaluateRiskGuard(action: "menu", targetLabel: item) {
                return ("menu", item, kw)
            }
        }
    case "quit":
        if step.force == true {
            let lbl = step.app ?? step.target ?? ""
            return ("quit --force", lbl, "quit --force")
        }
    default:
        break
    }
    return nil
}

func cmdRecipe(_ args: [String]) {
    guard let sub = args.first?.lowercased() else {
        fail("Usage: macos-ctl recipe <list|show|save|delete|run> [arguments]")
    }
    let subArgs = Array(args.dropFirst())

    switch sub {
    case "list":
        let parsed = parseArgs(subArgs)
        let asJson = parsed.hasFlag("--json")
        let dir = recipesDirectoryURL()
        var summaries: [RecipeSummary] = []

        if let files = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) {
            for file in files.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
                guard file.pathExtension.lowercased() == "json" else { continue }
                let name = file.deletingPathExtension().lastPathComponent
                if let data = try? Data(contentsOf: file),
                   let recipe = try? JSONDecoder().decode(Recipe.self, from: data) {
                    summaries.append(RecipeSummary(
                        name: name,
                        description: recipe.description ?? "",
                        params: recipe.params ?? [],
                        step_count: recipe.steps.count,
                        path: file.path
                    ))
                }
            }
        }

        if asJson {
            printJSON(summaries)
            return
        }
        if summaries.isEmpty {
            print("No saved recipes in \(dir.path).")
            return
        }
        for s in summaries {
            let paramPart = s.params.isEmpty ? "" : " params=[\(s.params.joined(separator: ", "))]"
            let descPart = s.description.isEmpty ? "" : " — \(s.description)"
            print("\(s.name) (\(s.step_count) steps)\(paramPart)\(descPart)")
        }

    case "show":
        guard let rawName = subArgs.first else {
            fail("Usage: macos-ctl recipe show <name>")
        }
        let url = recipeFileURL(named: rawName)
        guard FileManager.default.fileExists(atPath: url.path) else {
            fail("Recipe '\(rawName)' not found at \(url.path).")
        }
        guard let content = try? String(contentsOf: url, encoding: .utf8) else {
            fail("Failed to read recipe '\(rawName)'.")
        }
        print(content, terminator: content.hasSuffix("\n") ? "" : "\n")

    case "save":
        let parsed = parseArgs(subArgs)
        let fromStdin = parsed.hasFlag("--stdin")
        guard let rawName = parsed.positionals.first else {
            fail("Usage: macos-ctl recipe save <name> <file.json> | --stdin")
        }
        let name = validatedRecipeName(rawName)

        let rawData: Data
        if fromStdin {
            rawData = FileHandle.standardInput.readDataToEndOfFile()
            guard !rawData.isEmpty else {
                fail("No JSON data received on stdin.")
            }
        } else {
            guard parsed.positionals.count >= 2 else {
                fail("Usage: macos-ctl recipe save <name> <file.json> | --stdin")
            }
            let srcPath = NSString(string: parsed.positionals[1]).expandingTildeInPath
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: srcPath)) else {
                fail("Failed to read recipe file '\(srcPath)'.")
            }
            rawData = data
        }

        var recipe: Recipe
        do {
            recipe = try JSONDecoder().decode(Recipe.self, from: rawData)
        } catch {
            fail("Invalid recipe JSON: \(error.localizedDescription)")
        }
        if recipe.name == nil || recipe.name?.isEmpty == true {
            recipe.name = name
        }

        let dir = ensureRecipesDirectory()
        let dstURL = dir.appendingPathComponent("\(name).json")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        do {
            let encoded = try encoder.encode(recipe)
            try encoded.write(to: dstURL)
        } catch {
            fail("Failed to save recipe '\(name)': \(error.localizedDescription)")
        }
        print("Saved recipe '\(name)' (\(recipe.steps.count) steps) -> \(dstURL.path)")

    case "delete", "rm":
        guard let rawName = subArgs.first else {
            fail("Usage: macos-ctl recipe delete <name>")
        }
        let name = validatedRecipeName(rawName)
        let url = recipeFileURL(named: name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            fail("Recipe '\(name)' not found at \(url.path).")
        }
        do {
            try FileManager.default.removeItem(at: url)
        } catch {
            fail("Failed to delete recipe '\(name)': \(error.localizedDescription)")
        }
        print("Deleted recipe '\(name)'.")

    case "run":
        let parsed = parseArgs(subArgs, valueOptions: ["--param", "-p"])
        guard let rawName = parsed.positionals.first else {
            fail("Usage: macos-ctl recipe run <name> [--param key=val ...] [--dry-run] [--confirm-risky]")
        }
        let dryRun = parsed.hasFlag("--dry-run")
        let name = validatedRecipeName(rawName)
        let url = recipeFileURL(named: name)
        guard let data = try? Data(contentsOf: url) else {
            fail("Recipe '\(name)' not found at \(url.path).")
        }
        let recipe: Recipe
        do {
            recipe = try JSONDecoder().decode(Recipe.self, from: data)
        } catch {
            fail("Failed to decode recipe '\(name)': \(error.localizedDescription)")
        }

        // Parse --param key=val pairs
        var paramMap: [String: String] = [:]
        for pair in parsed.optionsList("--param", "-p") {
            guard let eqIdx = pair.firstIndex(of: "=") else {
                fail("Invalid --param '\(pair)'. Expected key=value format.")
            }
            let k = String(pair[..<eqIdx])
            let v = String(pair[pair.index(after: eqIdx)...])
            paramMap[k] = v
        }

        // Validate all declared and referenced parameters
        var requiredKeys: Set<String> = Set(recipe.params ?? [])
        for step in recipe.steps {
            for k in collectPlaceholdersInStep(step) {
                requiredKeys.insert(k)
            }
        }
        let missing = requiredKeys.filter { paramMap[$0] == nil }.sorted()
        if !missing.isEmpty {
            fail("Missing required recipe parameter(s): \(missing.joined(separator: ", ")). Pass via --param key=val.")
        }

        let resolvedSteps = recipe.steps.map { applyParamsToStep($0, params: paramMap) }

        if dryRun {
            print("[DRY-RUN] Recipe '\(name)' (\(resolvedSteps.count) steps):")
            for (idx, step) in resolvedSteps.enumerated() {
                print("  [\(idx + 1)/\(resolvedSteps.count)] \(describeRecipeStep(step))")
                if let risk = recipeStepRiskInfo(step) {
                    if GLOBAL_CONFIRM_RISKY {
                        print("      [RISK-GUARD ALLOWED via --confirm-risky: matched \"\(risk.keyword)\"]")
                    } else {
                        print("      [RISK-GUARD WOULD BLOCK: matched \"\(risk.keyword)\" in \"\(risk.target)\"]")
                    }
                }
            }
            return
        }

        print("Running recipe '\(name)' (\(resolvedSteps.count) steps)...")
        for (idx, step) in resolvedSteps.enumerated() {
            let act = step.resolvedAction
            print("[\(idx + 1)/\(resolvedSteps.count)] \(describeRecipeStep(step))")

            if let risk = recipeStepRiskInfo(step) {
                checkRiskGuard(action: risk.action, targetLabel: risk.target)
            }

            switch act {
            case "open":
                guard let t = step.target ?? step.app, !t.isEmpty else {
                    fail("Recipe step \(idx + 1) 'open' requires 'target'.")
                }
                cmdOpen([t])
            case "focus":
                guard let a = step.app ?? step.target, !a.isEmpty else {
                    fail("Recipe step \(idx + 1) 'focus' requires 'app' or 'target'.")
                }
                cmdFocus([a])
            case "menu":
                guard let a = step.app, !a.isEmpty else {
                    fail("Recipe step \(idx + 1) 'menu' requires 'app'.")
                }
                let items = step.items ?? step.path ?? []
                guard !items.isEmpty else {
                    fail("Recipe step \(idx + 1) 'menu' requires 'items'.")
                }
                cmdMenu([a] + items)
            case "press":
                if let t = step.target ?? step.ref, !t.isEmpty {
                    var pArgs = [t]
                    if let a = step.app, !a.isEmpty {
                        pArgs.append(contentsOf: ["--app", a])
                    }
                    cmdPress(pArgs)
                } else if let title = step.title, !title.isEmpty {
                    requireAccessibility()
                    checkRiskGuard(action: "press", targetLabel: title)
                    let app = resolveTargetApp(appName: step.app)
                    let matches = collectAXElements(app: app, maxDepth: 6, interactiveOnly: false, limit: 10, query: title)
                    if let first = matches.first,
                       let el = axElementByPath(pid: app.processIdentifier, path: first.path),
                       AXUIElementPerformAction(el, kAXPressAction as CFString) == .success {
                        print("Pressed \"\(first.title)\" (\(first.role)) via AXPress.")
                    } else if let x = step.x, let y = step.y {
                        activateApp(app)
                        usleep(80_000)
                        performClick(x: x, y: y, rightButton: false, clickCount: 1)
                        print("Pressed \"\(title)\" via recorded CGEvent click @(\(Int(x)), \(Int(y))).")
                    } else {
                        fail("No element matching '\(title)' found in \(app.localizedName ?? "Unknown").")
                    }
                } else if let x = step.x, let y = step.y {
                    if let a = step.app, let app = findRunningApp(named: a) {
                        activateApp(app)
                        usleep(80_000)
                    }
                    performClick(x: x, y: y, rightButton: false, clickCount: 1)
                    print("Clicked @(\(Int(x)), \(Int(y))).")
                } else {
                    fail("Recipe step \(idx + 1) 'press' requires 'target', 'ref', 'title', or (x, y).")
                }
            case "set":
                guard let tgt = step.ref ?? step.target, !tgt.isEmpty else {
                    fail("Recipe step \(idx + 1) 'set' requires 'ref' or 'target'.")
                }
                let txt = step.text ?? step.value ?? ""
                var sArgs = [tgt, txt]
                if let a = step.app, !a.isEmpty {
                    sArgs.append(contentsOf: ["--app", a])
                }
                if step.submit == true {
                    sArgs.append("--submit")
                }
                cmdSet(sArgs)
            case "type":
                let txt = step.text ?? step.value ?? ""
                var tArgs = [txt]
                if let d = step.delay_ms {
                    tArgs.append(contentsOf: ["--delay-ms", "\(d)"])
                }
                cmdType(tArgs)
            case "key":
                guard let c = step.combo ?? step.key, !c.isEmpty else {
                    fail("Recipe step \(idx + 1) 'key' requires 'combo' or 'key'.")
                }
                cmdKey([c])
            case "wait":
                let sec = max(0.0, step.seconds ?? step.duration ?? 1.0)
                usleep(UInt32(sec * 1_000_000))
            case "window-tile":
                guard let a = step.app ?? step.target, !a.isEmpty,
                      let layout = step.layout, !layout.isEmpty else {
                    fail("Recipe step \(idx + 1) 'window-tile' requires 'app' and 'layout'.")
                }
                var wArgs = [a, layout]
                if let t = step.title, !t.isEmpty {
                    wArgs.append(contentsOf: ["--title", t])
                }
                if let d = step.display {
                    wArgs.append(contentsOf: ["--display", "\(d)"])
                }
                cmdWindowTile(wArgs)
            case "quit":
                guard let a = step.app ?? step.target, !a.isEmpty else {
                    fail("Recipe step \(idx + 1) 'quit' requires 'app'.")
                }
                var qArgs = [a]
                if step.force == true { qArgs.append("--force") }
                cmdQuit(qArgs)
            default:
                fail("Unknown recipe step action '\(act)' at step \(idx + 1).")
            }
        }
        print("Recipe '\(name)' completed.")

    default:
        fail("Unknown recipe subcommand '\(sub)'. Use: list, show, save, delete, run.")
    }
}

// MARK: - Help & Dispatch

func printHelp() {
    print("""
    macos-ctl — Native macOS desktop automation CLI (v3.0.0)

    USAGE:
      macos-ctl [global-options] <command> [arguments] [options]

    GLOBAL OPTIONS:
      --confirm-risky
          Allow destructive/external-send actions blocked by Risk-Guard
          (only after explicit user approval).
      --trace <file.jsonl>
          Append a compact JSONL audit trace entry for this invocation
          (also configurable via MACOS_CTL_TRACE env var).

    COMMANDS:
      doctor [--json]
          Check Accessibility, Screen Recording & Input Monitoring permissions,
          macOS version, screens, and frontmost application.

      apps [--json]
          List running GUI applications (name, bundleId, pid, active, hidden).

      open <target>
          Open a URL, existing file path, or macOS application.

      focus <app>
          Activate and unhide a running application (name or bundleId).

      quit <app> [--force]
          Terminate (or force-terminate, guarded by Risk-Guard) a running app.

      windows [--app <name>] [--json]
          List visible on-screen windows with IDs and bounds.

      window-move <app> <x> <y> [--title <substr>]
          Move an application's window to (x, y).

      window-resize <app> <w> <h> [--title <substr>]
          Resize an application's window to width x height.

      window-tile <app> <left|right|top|bottom|maximize|center> [--title <substr>] [--display <1-based>]
          Tile an application's window against a display's visible frame.

      tree [--app <name>] [--depth <N>] [--interactive] [--limit <N>] [--json]
          Inspect the Accessibility tree and save element refs (e1..eN) to
          /tmp/macos-ctl-refs.json.

      find <query> [--app <name>] [--role <AXRole>] [--json]
          Search the Accessibility tree and update /tmp/macos-ctl-refs.json.

      press <ref|query> [--app <name>]
          Press an element ref (e.g. e3) or search query via AXPress (with
          CGEvent click fallback; guarded by Risk-Guard).

      set <ref> <text> [--submit]
          Focus and set text on an element ref via AXValue (with click +
          Cmd+A + Unicode typing fallback).

      menu <app> <item1> [item2 ...]
          Trigger a menu bar item path via Accessibility (guarded by Risk-Guard).

      click <x> <y> [--right] [--double] [--count <N>]
          Perform a mouse click at logical screen coordinates.

      move-mouse <x> <y>
          Move the mouse cursor to (x, y).

      drag <x1> <y1> <x2> <y2> [--duration <sec>]
          Drag from (x1, y1) to (x2, y2).

      scroll <dx> <dy> [--x <x> --y <y>]
          Scroll horizontally (dx) and vertically (dy).

      type <text> [--delay-ms <N>]
          Type full Unicode text via CGEvent keyboardSetUnicodeString.

      key <combo>
          Send key combinations (e.g. cmd+shift+s, alt+tab, return, f5).

      clipboard <get|set> [text]
          Read or write the system pasteboard.

      notify <title> <message>
          Display a macOS notification.

      shortcut <name> [--input <text>]
          Run a macOS Shortcut by name.

      screenshot <output.png> [--app <name>] [--region x,y,w,h] [--grid] [--annotate]
          Capture a screenshot, optionally overlaying a logical coordinate grid
          and/or Set-of-Mark (SoM) interactive element bounding boxes + refs.

      record [--duration <sec>] [--output <file.json>] [--app <name>]
          Record mouse clicks (with AX role/title) and typing into a JSON recipe
          (requires Input Monitoring; default 15s, max 120s, Ctrl-C to stop).

      recipe list [--json]
      recipe show <name>
      recipe save <name> <file.json> | --stdin
      recipe delete <name>
      recipe run <name> [--param key=val ...] [--dry-run] [--confirm-risky]
          Manage and execute parameterized self-learning automation recipes in
          ~/.config/antigravity-macos/recipes/.
    """)
}

// MARK: - Main Entry

func extractGlobalFlags(_ rawArgs: [String]) -> [String] {
    var filtered: [String] = []
    var i = 0
    var passThrough = false
    while i < rawArgs.count {
        let arg = rawArgs[i]
        if passThrough {
            filtered.append(arg)
            i += 1
            continue
        }
        if arg == "--" {
            passThrough = true
            filtered.append(arg)
            i += 1
            continue
        }
        if arg == "--confirm-risky" {
            GLOBAL_CONFIRM_RISKY = true
            i += 1
            continue
        }
        if arg == "--trace" {
            if i + 1 < rawArgs.count {
                GLOBAL_TRACE_PATH = NSString(string: rawArgs[i + 1]).expandingTildeInPath
                i += 2
                continue
            } else {
                fail("Option --trace requires a file path.")
            }
        }
        if arg.hasPrefix("--trace=") {
            let val = String(arg.dropFirst("--trace=".count))
            GLOBAL_TRACE_PATH = NSString(string: val).expandingTildeInPath
            i += 1
            continue
        }
        filtered.append(arg)
        i += 1
    }
    return filtered
}

GLOBAL_START_TIME = CFAbsoluteTimeGetCurrent()
let allArgs = extractGlobalFlags(Array(CommandLine.arguments.dropFirst()))
guard let cmd = allArgs.first else {
    printHelp()
    exit(0)
}
let rest = Array(allArgs.dropFirst())
GLOBAL_TRACE_COMMAND = cmd.lowercased()
GLOBAL_TRACE_ARGS = rest

switch cmd.lowercased() {
case "--help", "-h", "help":
    printHelp()
case "doctor":
    cmdDoctor(rest)
case "apps":
    cmdApps(rest)
case "open":
    cmdOpen(rest)
case "focus":
    cmdFocus(rest)
case "quit":
    cmdQuit(rest)
case "windows":
    cmdWindows(rest)
case "window-move":
    cmdWindowMove(rest)
case "window-resize":
    cmdWindowResize(rest)
case "window-tile":
    cmdWindowTile(rest)
case "tree":
    cmdTree(rest)
case "find":
    cmdFind(rest)
case "press":
    cmdPress(rest)
case "set":
    cmdSet(rest)
case "menu":
    cmdMenu(rest)
case "click":
    cmdClick(rest)
case "move-mouse":
    cmdMoveMouse(rest)
case "drag":
    cmdDrag(rest)
case "scroll":
    cmdScroll(rest)
case "type":
    cmdType(rest)
case "key":
    cmdKey(rest)
case "clipboard":
    cmdClipboard(rest)
case "notify":
    cmdNotify(rest)
case "shortcut":
    cmdShortcut(rest)
case "screenshot":
    cmdScreenshot(rest)
case "record":
    cmdRecord(rest)
case "recipe":
    cmdRecipe(rest)
default:
    printErr("Unknown command: '\(cmd)'\n")
    printHelp()
    exitWithTrace(1)
}

writeTrace(exitCode: 0)

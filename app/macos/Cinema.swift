import AppKit
import WebKit
import UniformTypeIdentifiers
import Darwin

@main
final class CinemaApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var clipboardShortcutsMonitor: Any?
    private var server: Process?
    private var instanceLock: Int32 = -1
    private var terminationPending = false
    private var choosingVideos = false
    private var startupSucceeded = false
    private var fullScreenTransition = false
    private var queuedFullScreen: Bool?
    private let localOrigin = URL(string: "http://127.0.0.1:8765")!
    private let importToken = UUID().uuidString + UUID().uuidString

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard acquireInstanceLock() else {
            if let identifier = Bundle.main.bundleIdentifier {
                NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
                    .first { $0.processIdentifier != getpid() }?.activate(options: [.activateIgnoringOtherApps])
            }
            NSApp.terminate(nil)
            return
        }
        let resources = Bundle.main.resourceURL!
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3", "/usr/bin/python3"]
        guard let python = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            let alert = NSAlert()
            alert.messageText = "Python 3 est nécessaire"
            alert.informativeText = "Installe Python 3, FFmpeg et yt-dlp comme indiqué dans le guide de Fauteuil."
            alert.runModal()
            NSApp.terminate(nil)
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-m", "fauteuil"]
        process.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        environment["CINEMA_IMPORT_TOKEN"] = importToken
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            server = process
        } catch {
            NSAlert(error: error).runModal()
            NSApp.terminate(nil)
            return
        }

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        if #available(macOS 12.3, *) {
            config.preferences.isElementFullscreenEnabled = true
        }
        config.userContentController.add(self, name: "importLocalVideos")
        config.userContentController.add(self, name: "playerFullScreen")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.underPageBackgroundColor = .black
        let savedZoom = UserDefaults.standard.object(forKey: "interfaceZoom") as? Double ?? 1
        setInterfaceZoom(savedZoom)
        let screen = NSScreen.main?.visibleFrame.size ?? NSSize(width: 1280, height: 820)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: min(1280, screen.width * 0.94), height: min(820, screen.height * 0.92)),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false
        )
        window.contentMinSize = NSSize(width: 420, height: 320)
        window.collectionBehavior.insert(.fullScreenPrimary)
        window.title = "Fauteuil"
        window.backgroundColor = .black
        window.isRestorable = false
        installMenus()
        window.delegate = self
        window.center()
        window.setFrameUsingName("FauteuilMainWindow")
        window.setFrameAutosaveName("FauteuilMainWindow")
        let content = NSView(frame: NSRect(origin: .zero, size: window.contentLayoutRect.size))
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.black.cgColor
        window.contentView = content
        webView.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.topAnchor.constraint(equalTo: content.topAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor)
        ])
        clipboardShortcutsMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, event.window === self.window,
                  self.window.firstResponder === self.webView,
                  event.modifierFlags.intersection([.command, .shift, .option, .control]) == .command
            else { return event }

            let action: Selector
            switch event.charactersIgnoringModifiers?.lowercased() {
            case "c": action = #selector(NSText.copy(_:))
            case "v": action = #selector(NSText.paste(_:))
            default: return event
            }
            return NSApp.sendAction(action, to: self.webView, from: nil) ? nil : event
        }
        window.makeKeyAndOrderFront(nil)
        webView.loadHTMLString("""
        <!doctype html><html lang="fr"><meta charset="utf-8">
        <title>Fauteuil</title>
        <style>
        body { min-height: 100vh; margin: 0; display: grid; place-items: center;
               background: #111; color: #eee; font: 16px -apple-system, sans-serif; }
        </style><p>Ouverture de ta médiathèque…</p></html>
        """, baseURL: nil)

        process.terminationHandler = { [weak self] _ in
            Task { @MainActor in
                guard let self, self.startupSucceeded, !self.terminationPending else { return }
                self.showServerFailure("Le serveur local de Fauteuil s’est arrêté. Relance l’application.")
            }
        }
        Task { @MainActor [weak self] in
            await self?.loadWhenServerIsReady(process)
        }
    }

    private func acquireInstanceLock() -> Bool {
        let path = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("io.github.nicopfr.fauteuil.lock").path
        let fd = open(path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { return false }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            close(fd)
            return false
        }
        instanceLock = fd
        return true
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window?.makeKeyAndOrderFront(nil)
        return true
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        guard startupSucceeded else { return }
        webView.evaluateJavaScript("window.refreshYouTubeOnReturn?.()")
    }

    private func loadWhenServerIsReady(_ process: Process) async {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 0.4
        configuration.timeoutIntervalForResource = 1
        let session = URLSession(configuration: configuration)
        defer { session.finishTasksAndInvalidate() }
        var request = URLRequest(url: localOrigin.appendingPathComponent("api/health"))
        request.setValue(importToken, forHTTPHeaderField: "X-Cinema-Import")
        request.cachePolicy = .reloadIgnoringLocalCacheData

        // Allow schema initialization, the worker handshake and its shutdown grace period.
        let deadline = ProcessInfo.processInfo.systemUptime + 25
        while process.isRunning && !terminationPending && ProcessInfo.processInfo.systemUptime < deadline {
            if let (_, response) = try? await session.data(for: request),
               (response as? HTTPURLResponse)?.statusCode == 200, process.isRunning, !terminationPending {
                startupSucceeded = true
                webView.load(URLRequest(url: localOrigin))
                return
            }
            try? await Task.sleep(for: .milliseconds(100))
        }
        if !terminationPending {
            showServerFailure("Impossible de démarrer le serveur local. Ferme les autres instances de Fauteuil puis relance l’application.")
        }
    }

    private func showServerFailure(_ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Médiathèque indisponible"
        alert.informativeText = message
        alert.addButton(withTitle: "Quitter Fauteuil")
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.protocol == "http",
              origin.host == localOrigin.host, origin.port == localOrigin.port else { return }
        switch message.name {
        case "importLocalVideos":
            showImportPanel()
        case "playerFullScreen":
            if let enabled = message.body as? Bool {
                setFullScreen(enabled)
            } else if message.body as? String == "status" {
                publishFullScreen()
            }
        default:
            break
        }
    }

    private func setFullScreen(_ enabled: Bool) {
        if fullScreenTransition {
            queuedFullScreen = enabled
        } else if window.styleMask.contains(.fullScreen) != enabled {
            fullScreenTransition = true
            window.toggleFullScreen(nil)
        } else {
            publishFullScreen()
        }
    }

    private func publishFullScreen(failed: Bool = false) {
        let enabled = window.styleMask.contains(.fullScreen)
        webView.evaluateJavaScript("window.cinemaFullScreenChanged?.(\(enabled), \(failed))")
    }

    private func finishFullScreenTransition(failed: Bool = false) {
        fullScreenTransition = false
        let next = queuedFullScreen
        queuedFullScreen = nil
        publishFullScreen(failed: failed)
        if let next, !failed { setFullScreen(next) }
    }

    func windowWillEnterFullScreen(_ notification: Notification) { fullScreenTransition = true }
    func windowWillExitFullScreen(_ notification: Notification) { fullScreenTransition = true }
    func windowDidEnterFullScreen(_ notification: Notification) { finishFullScreenTransition() }
    func windowDidExitFullScreen(_ notification: Notification) { finishFullScreenTransition() }
    func windowDidFailToEnterFullScreen(_ window: NSWindow) { finishFullScreenTransition(failed: true) }
    func windowDidFailToExitFullScreen(_ window: NSWindow) { finishFullScreenTransition(failed: true) }

    @objc private func showImportPanel() {
        guard startupSucceeded, !choosingVideos, window.attachedSheet == nil else { return }
        choosingVideos = true
        let panel = NSOpenPanel()
        panel.title = "Ajouter des vidéos à Fauteuil"
        panel.prompt = "Ajouter"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = ["mp4", "mov", "m4v", "webm"].compactMap { UTType(filenameExtension: $0) }
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self else { return }
            guard response == .OK else {
                self.choosingVideos = false
                return
            }
            let paths = panel.urls.map(\.path)
            Task { @MainActor in await self.importLocalVideos(paths) }
        }
    }

    private func installMenus() {
        let main = NSMenu()
        func section(_ title: String) -> NSMenu {
            let item = NSMenuItem()
            let menu = NSMenu(title: title)
            item.submenu = menu
            main.addItem(item)
            return menu
        }
        func add(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String, target: AnyObject? = nil) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.target = target
            menu.addItem(item)
        }

        let app = section("Fauteuil")
        add(app, "Quitter Fauteuil", #selector(NSApplication.terminate(_:)), "q", target: NSApp)
        let file = section("Fichier")
        add(file, "Ajouter des vidéos…", #selector(showImportPanel), "o", target: self)
        add(file, "Fermer la fenêtre", #selector(NSWindow.performClose(_:)), "w", target: window)

        let edit = section("Édition")
        add(edit, "Annuler", Selector(("undo:")), "z")
        add(edit, "Couper", #selector(NSText.cut(_:)), "x")
        add(edit, "Copier", #selector(NSText.copy(_:)), "c")
        add(edit, "Coller", #selector(NSText.paste(_:)), "v")
        add(edit, "Tout sélectionner", #selector(NSText.selectAll(_:)), "a")
        edit.addItem(.separator())
        add(edit, "Rechercher dans Fauteuil", #selector(focusSearch), "f", target: self)

        let view = section("Présentation")
        add(view, "Agrandir", #selector(zoomIn), "=", target: self)
        add(view, "Réduire", #selector(zoomOut), "-", target: self)
        add(view, "Taille réelle", #selector(resetZoom), "0", target: self)
        let fullscreen = NSMenuItem(title: "Basculer en plein écran", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullscreen.keyEquivalentModifierMask = [.command, .control]
        fullscreen.target = window
        view.addItem(fullscreen)
        NSApp.mainMenu = main
    }

    @objc private func focusSearch() {
        guard startupSucceeded, window.attachedSheet == nil else { return }
        window.makeFirstResponder(webView)
        Task { @MainActor in
            let result = try? await webView.evaluateJavaScript("window.focusCinemaSearch?.() ?? false")
            if result as? Bool != true {
                var components = URLComponents(url: localOrigin, resolvingAgainstBaseURL: false)!
                components.queryItems = [URLQueryItem(name: "focus", value: "search")]
                if let url = components.url { webView.load(URLRequest(url: url)) }
            }
        }
    }

    private func setInterfaceZoom(_ value: Double) {
        let zoom = value.isFinite ? max(0.7, min(1.6, value)) : 1
        webView.pageZoom = zoom
        UserDefaults.standard.set(zoom, forKey: "interfaceZoom")
    }

    @objc private func zoomIn() { setInterfaceZoom(webView.pageZoom + 0.1) }
    @objc private func zoomOut() { setInterfaceZoom(webView.pageZoom - 0.1) }
    @objc private func resetZoom() { setInterfaceZoom(1) }

    private struct ImportResult: Decodable {
        let ok: Bool
        let added: Int?
        let skipped: Int?
        let error: String?
    }

    private func importLocalVideos(_ paths: [String]) async {
        defer { choosingVideos = false }
        var added = 0
        var skipped = 0
        var errorMessage = ""
        do {
            var request = URLRequest(url: localOrigin.appendingPathComponent("api/local/import"), timeoutInterval: 30)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue(importToken, forHTTPHeaderField: "X-Cinema-Import")
            request.httpBody = try JSONEncoder().encode(["paths": paths])
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let result = try JSONDecoder().decode(ImportResult.self, from: data)
            if result.ok {
                added = result.added ?? 0
                skipped = result.skipped ?? 0
            } else {
                errorMessage = result.error ?? "Impossible d’ajouter ces vidéos."
            }
        } catch {
            errorMessage = "Impossible de joindre la médiathèque pour ajouter ces vidéos."
        }
        _ = try? await webView.callAsyncJavaScript(
            "if (window.localImportFinished) await window.localImportFinished(added, skipped, error);",
            arguments: ["added": added, "skipped": skipped, "error": errorMessage],
            in: nil, contentWorld: .page
        )
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // Keep WebKit alive while applicationShouldTerminate saves the position.
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let webView else { return .terminateNow }
        guard !terminationPending else { return .terminateLater }
        terminationPending = true

        let finish = { [weak self] in
            guard let self, self.terminationPending else { return }
            self.terminationPending = false
            sender.reply(toApplicationShouldTerminate: true)
        }
        Task { @MainActor in
            _ = try? await webView.callAsyncJavaScript(
                "if (window.savePlaybackBeforeQuit) await window.savePlaybackBeforeQuit();",
                arguments: [:], in: nil, contentWorld: .page
            )
            finish()
        }
        // Let WebKit flush the last position while bounding the wait on a failed gateway.
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: finish)
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let clipboardShortcutsMonitor { NSEvent.removeMonitor(clipboardShortcutsMonitor) }
        if let server, server.isRunning {
            server.terminate()
        }
        if instanceLock >= 0 { close(instanceLock) }
    }

    static func main() {
        let app = NSApplication.shared
        let delegate = CinemaApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}

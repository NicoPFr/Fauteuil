import Foundation

@main
enum ApplicationInstallationTests {
    static func main() throws {
        let files = FileManager.default
        let root = files.temporaryDirectory.appendingPathComponent("fauteuil-install-test-\(UUID().uuidString)")
        try files.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? files.removeItem(at: root) }
        let source = URL(fileURLWithPath: CommandLine.arguments[1])
        let installation = ApplicationInstallation(
            source: source,
            userApplications: root.appendingPathComponent("User Applications"),
            systemApplications: root.appendingPathComponent("System Applications"),
            identifier: "io.github.nicopfr.fauteuil"
        )

        let incoming = try require(installation.application(at: source))
        precondition(!installation.sourceIsInApplications && installation.installed() == nil)
        let destination = try installation.install(replacing: nil)
        precondition(destination == installation.destination)
        let firstCopy = try require(installation.installed())
        precondition(firstCopy.compare(to: incoming) == .orderedSame)
        precondition(files.fileExists(atPath: source.path))
        try expectFailure { _ = try installation.install(replacing: firstCopy) } // Same version.
        try expectFailure { _ = try installation.install(replacing: nil) } // Occupied destination.
        precondition(installation.installed()?.isSameFile(as: firstCopy) == true)

        try files.createDirectory(at: installation.systemApplications, withIntermediateDirectories: false)
        let previousPath = installation.systemApplications.appendingPathComponent("Fauteuil.app")
        try files.moveItem(at: destination, to: previousPath)
        try writeVersion("1.0.0", at: previousPath)
        let previous = try require(installation.installed())
        precondition(previous.url.path == previousPath.path)
        let updated = try installation.install(replacing: previous)
        precondition(updated.path == previousPath.path)
        precondition(installation.installed()?.compare(to: incoming) == .orderedSame)
        precondition(!files.fileExists(atPath: destination.path))
        let updatedCopy = try require(installation.installed())
        precondition(!updatedCopy.isSameFile(as: previous))
        try expectFailure { _ = try installation.install(replacing: previous) } // A stale update.

        try writeVersion("99.0.0", at: previousPath)
        let newer = try require(installation.installed())
        try expectFailure { _ = try installation.install(replacing: newer) }
        precondition(installation.installed()?.version == "99.0.0")
        try writeVersion("1.0.0", at: previousPath)

        let invalidSource = root.appendingPathComponent("Modified.app")
        try files.copyItem(at: source, to: invalidSource)
        try writeVersion("100.0.0", at: invalidSource)
        let invalid = ApplicationInstallation(source: invalidSource,
                                              userApplications: installation.userApplications,
                                              systemApplications: installation.systemApplications,
                                              identifier: installation.identifier)
        let old = try require(installation.installed())
        try expectFailure { _ = try invalid.install(replacing: old) }
        precondition(installation.installed()?.isSameFile(as: old) == true)
        precondition(installation.installed()?.version == "1.0.0")

        try files.createSymbolicLink(at: destination, withDestinationURL: previousPath)
        try expectFailure { _ = try installation.install(replacing: nil) }
        let aliasTarget = try files.destinationOfSymbolicLink(atPath: destination.path)
        precondition(aliasTarget == previousPath.path)
        let renamed = installation.systemApplications.appendingPathComponent("Mon Fauteuil.app")
        try files.moveItem(at: previousPath, to: renamed)
        precondition(installation.installed(registered: [source]) == nil)
        precondition(installation.installed(registered: [source, renamed])?.url.path == renamed.path)
        print("PASS: install, in-place update, equal and newer versions, stale target, invalid signature and collisions")
    }

    private static func require<T>(_ value: T?) throws -> T {
        guard let value else { throw CocoaError(.fileReadUnknown) }
        return value
    }

    private static func expectFailure(_ action: () throws -> Void) throws {
        do { try action() } catch { return }
        preconditionFailure("The existing application must be preserved")
    }

    private static func writeVersion(_ version: String, at app: URL) throws {
        let path = app.appendingPathComponent("Contents/Info.plist")
        let data = try Data(contentsOf: path)
        let info = try PropertyListSerialization.propertyList(from: data, format: nil)
        var values = try require(info as? [String: Any])
        values["CFBundleShortVersionString"] = version
        try PropertyListSerialization.data(fromPropertyList: values, format: .xml, options: 0).write(to: path)
    }
}

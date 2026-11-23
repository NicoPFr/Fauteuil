import Darwin
import Foundation

struct ApplicationInstallation {
    struct Application {
        let url: URL
        let version: String
        let build: String
        let inode: NSNumber
        let device: NSNumber

        func compare(to other: Application) -> ComparisonResult {
            let order = version.compare(other.version, options: .numeric)
            return order == .orderedSame ? build.compare(other.build, options: .numeric) : order
        }

        func isSameFile(as other: Application) -> Bool {
            inode == other.inode && device == other.device
        }
    }

    let source: URL
    let userApplications: URL
    let systemApplications: URL
    let identifier: String
    private let files = FileManager.default

    var destination: URL {
        userApplications.appendingPathComponent("Fauteuil.app", isDirectory: true)
    }

    var sourceIsInApplications: Bool {
        isInApplications(source)
    }

    func installed(registered: [URL] = []) -> Application? {
        let standard = [destination, systemApplications.appendingPathComponent("Fauteuil.app", isDirectory: true)]
        return (standard + registered.filter(isInApplications))
            .compactMap(application)
            .max { $0.compare(to: $1) == .orderedAscending }
    }

    private func isInApplications(_ url: URL) -> Bool {
        let path = url.resolvingSymlinksInPath().path
        return [userApplications, systemApplications].contains {
            path.hasPrefix($0.resolvingSymlinksInPath().path + "/")
        }
    }

    func application(at url: URL) -> Application? {
        guard let attributes = try? files.attributesOfItem(atPath: url.path),
              attributes[.type] as? FileAttributeType == .typeDirectory,
              let inode = attributes[.systemFileNumber] as? NSNumber,
              let device = attributes[.systemNumber] as? NSNumber,
              let data = try? Data(contentsOf: url.appendingPathComponent("Contents/Info.plist")),
              let info = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any],
              info["CFBundleIdentifier"] as? String == identifier,
              let version = info["CFBundleShortVersionString"] as? String,
              let build = info["CFBundleVersion"] as? String else { return nil }
        return Application(url: url, version: version, build: build, inode: inode, device: device)
    }

    func install(replacing previous: Application?) throws -> URL {
        let target = previous?.url ?? destination
        let directory = target.deletingLastPathComponent()
        try files.createDirectory(at: directory, withIntermediateDirectories: true)
        let temporary = directory.appendingPathComponent(".Fauteuil-\(UUID().uuidString)", isDirectory: true)
        try files.createDirectory(at: temporary, withIntermediateDirectories: false,
                                  attributes: [.posixPermissions: 0o700])
        defer { try? files.removeItem(at: temporary) }
        let staged = temporary.appendingPathComponent("Fauteuil.app", isDirectory: true)
        try files.copyItem(at: source, to: staged)
        try verifySignature(at: staged)

        // Stage on the destination volume. APFS exchanges the two bundles in one operation.
        // An interrupted copy leaves the existing app intact, and fresh installs refuse collisions.
        let flags: UInt32
        if let previous {
            guard let current = application(at: target), current.isSameFile(as: previous) else {
                throw CocoaError(.fileWriteFileExists)
            }
            guard let incoming = application(at: staged), incoming.compare(to: current) == .orderedDescending else {
                throw CocoaError(.fileWriteUnknown)
            }
            flags = UInt32(RENAME_SWAP)
        } else {
            flags = UInt32(RENAME_EXCL)
        }
        guard renamex_np(staged.path, target.path, flags) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        return target
    }

    private func verifySignature(at url: URL) throws {
        let verifier = Process()
        verifier.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
        verifier.arguments = ["--verify", "--strict", url.path]
        verifier.standardOutput = FileHandle.nullDevice
        verifier.standardError = FileHandle.nullDevice
        try verifier.run()
        verifier.waitUntilExit()
        guard verifier.terminationStatus == 0 else { throw CocoaError(.fileReadCorruptFile) }
    }
}

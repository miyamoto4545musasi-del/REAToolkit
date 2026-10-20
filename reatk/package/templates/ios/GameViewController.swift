import UIKit
import UniformTypeIdentifiers

/// Host screen for the converted title.
///
/// Reads `Resources/reatk/manifest.json`, puts the two DS screens where the
/// layout says, draws the touch controls, and drives the core from a
/// `CADisplayLink`. Frames are presented through `CALayer.contents`, which is
/// enough for a 256x384 image scaled up; swap in Metal if you need more.
final class GameViewController: UIViewController {

    private var manifest: [String: Any] = [:]
    private let topScreen = CALayer()
    private let bottomScreen = CALayer()
    private var controls: ControlOverlay?
    private let status = UILabel()
    private let audio = AudioOutput()
    private var displayLink: CADisplayLink?
    private var colourSpace = CGColorSpaceCreateDeviceRGB()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        manifest = Self.loadManifest()

        for layer in [topScreen, bottomScreen] {
            layer.magnificationFilter = .nearest
            layer.backgroundColor = UIColor.darkGray.cgColor
            view.layer.addSublayer(layer)
        }

        let overlay = ControlOverlay(manifest: manifest)
        overlay.frame = view.bounds
        overlay.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(overlay)
        controls = overlay

        status.numberOfLines = 0
        status.textColor = .white
        status.backgroundColor = UIColor.black.withAlphaComponent(0.7)
        status.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        status.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(status)
        NSLayoutConstraint.activate([
            status.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            status.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            status.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
        ])

        start()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        layoutScreens()
    }

    // -- lifecycle -------------------------------------------------------

    private func start() {
        let rom = manifest["rom"] as? [String: Any] ?? [:]
        let embedded = rom["embedded"] as? Bool ?? false
        let documents = FileManager.default
            .urls(for: .documentDirectory, in: .userDomainMask)[0]

        var romPath: String?
        if embedded, let relative = rom["path"] as? String {
            romPath = Bundle.main.bundleURL.appendingPathComponent(relative).path
        } else {
            // Nothing embedded: use the first ROM dropped into Documents.
            romPath = (try? FileManager.default.contentsOfDirectory(
                at: documents, includingPropertiesForKeys: nil
            ))?.first { ["nds", "srl", "gba"].contains($0.pathExtension.lowercased()) }?.path
        }

        if let romPath {
            do {
                try EmuCore.shared.open(romPath: romPath, systemDirectory: documents.path)
                status.isHidden = true
                audio.start(sampleRate: EmuCore.shared.sampleRate)
            } catch {
                status.text = """
                \(error.localizedDescription)
                Showing a test pattern. Link a core and set EmuCore.shared.
                """
            }
        } else {
            status.text = """
            No ROM found. Copy one you own into this app's Documents folder
            through Files, then reopen the app.
            """
        }

        let link = CADisplayLink(target: self, selector: #selector(step))
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    deinit {
        displayLink?.invalidate()
        audio.stop()
        EmuCore.shared.close()
    }

    @objc private func step() {
        if let input = controls?.currentInput {
            EmuCore.shared.set(buttons: input.buttons)
            EmuCore.shared.set(pointer: input.stylus)
        }
        guard let frame = EmuCore.shared.runFrame() else { return }
        present(frame)
    }

    // -- presentation ----------------------------------------------------

    private func present(_ frame: EmulatedFrame) {
        guard let provider = CGDataProvider(data: frame.pixels as CFData),
              let image = CGImage(
                  width: frame.width,
                  height: frame.height,
                  bitsPerComponent: 8,
                  bitsPerPixel: 32,
                  bytesPerRow: frame.width * 4,
                  space: colourSpace,
                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                  provider: provider,
                  decode: nil,
                  shouldInterpolate: false,
                  intent: .defaultIntent
              )
        else { return }

        // One image, shown twice: the top half on one layer, the bottom on the
        // other. contentsRect is in unit coordinates.
        topScreen.contents = image
        topScreen.contentsRect = CGRect(x: 0, y: 0, width: 1, height: 0.5)
        bottomScreen.contents = image
        bottomScreen.contentsRect = CGRect(x: 0, y: 0.5, width: 1, height: 0.5)
    }

    private func layoutScreens() {
        let key = view.bounds.width > view.bounds.height ? "landscape" : "portrait"
        let screens = (manifest["controls"] as? [String: Any])
            .flatMap { $0[key] as? [String: Any] }
            .flatMap { $0["screens"] as? [String: Any] }

        func rect(_ name: String, fallback: CGRect) -> CGRect {
            guard let values = screens?[name] as? [Double], values.count == 4 else {
                return fallback
            }
            return CGRect(
                x: values[0] * view.bounds.width,
                y: values[1] * view.bounds.height,
                width: values[2] * view.bounds.width,
                height: values[3] * view.bounds.height
            )
        }

        let half = view.bounds.height / 2
        topScreen.frame = rect(
            "top", fallback: CGRect(x: 0, y: 0, width: view.bounds.width, height: half)
        )
        bottomScreen.frame = rect(
            "bottom",
            fallback: CGRect(x: 0, y: half, width: view.bounds.width, height: half)
        )
        controls?.screenRect = bottomScreen.frame
    }

    private static func loadManifest() -> [String: Any] {
        guard
            let url = Bundle.main.url(
                forResource: "manifest", withExtension: "json", subdirectory: "reatk"
            ),
            let data = try? Data(contentsOf: url),
            let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return parsed
    }
}

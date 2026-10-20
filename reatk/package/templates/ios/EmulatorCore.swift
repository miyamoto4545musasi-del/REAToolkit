import CoreGraphics
import Foundation

/// One emulated frame: RGBA bytes plus its size.
struct EmulatedFrame {
    let pixels: Data
    let width: Int
    let height: Int
}

/// What the app needs from an emulation core.
///
/// iOS will not `dlopen` a core in a sideloaded app, so a core has to be
/// linked in as a static library or an embedded framework. Rather than pretend
/// otherwise, the app talks to this protocol: implement it over the core you
/// linked (calling `retro_init`, `retro_load_game`, `retro_run` and friends)
/// and assign it to `EmuCore.shared` in `AppDelegate`.
protocol EmulatorCore: AnyObject {
    var isRunning: Bool { get }
    var sampleRate: Double { get }

    func open(romPath: String, systemDirectory: String) throws
    func runFrame() -> EmulatedFrame?
    func set(buttons: UInt16)
    /// Stylus position in frame pixels, or nil to lift it.
    func set(pointer: CGPoint?)
    /// Fill `into` with interleaved stereo Int16 samples; return how many frames.
    func readAudio(into: UnsafeMutablePointer<Int16>, frames: Int) -> Int
    func close()
}

enum CoreError: Error, LocalizedError {
    case notLinked

    var errorDescription: String? {
        switch self {
        case .notLinked:
            return "No emulation core is linked into this build."
        }
    }
}

/// Stands in until a real core is linked.
///
/// It produces a moving test pattern so the app runs, the screens light up and
/// the controls can be checked before any core exists.
final class PlaceholderCore: EmulatorCore {

    private(set) var isRunning = false
    let sampleRate: Double = 32768
    private var tick = 0
    private let width = 256
    private let height = 384

    func open(romPath: String, systemDirectory: String) throws {
        throw CoreError.notLinked
    }

    func runFrame() -> EmulatedFrame? {
        tick += 1
        var pixels = Data(count: width * height * 4)
        pixels.withUnsafeMutableBytes { raw in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return }
            for y in 0..<height {
                for x in 0..<width {
                    let checker = ((x + tick) / 16 + y / 16) % 2 == 0
                    let value: UInt8 = checker ? 0x40 : 0xC0
                    let offset = (y * width + x) * 4
                    base[offset] = value
                    base[offset + 1] = value / 2
                    base[offset + 2] = 0xFF - value
                    base[offset + 3] = 0xFF
                }
            }
        }
        return EmulatedFrame(pixels: pixels, width: width, height: height)
    }

    func set(buttons: UInt16) {}
    func set(pointer: CGPoint?) {}
    func readAudio(into: UnsafeMutablePointer<Int16>, frames: Int) -> Int { 0 }
    func close() { isRunning = false }
}

/// The core the app talks to. Replace in `AppDelegate` once you link one.
enum EmuCore {
    static var shared: EmulatorCore = PlaceholderCore()
}

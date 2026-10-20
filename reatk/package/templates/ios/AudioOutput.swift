import AVFoundation

/// Pulls samples from the core and plays them.
///
/// `AVAudioSourceNode` asks for audio on a real-time thread, so this only
/// copies what the core already has ready and pads with silence otherwise -
/// never blocks, never allocates.
final class AudioOutput {

    private let engine = AVAudioEngine()
    private var source: AVAudioSourceNode?

    func start(sampleRate: Double) {
        let format = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: sampleRate,
            channels: 2,
            interleaved: true
        )
        guard let format else { return }

        let node = AVAudioSourceNode(format: format) { _, _, frameCount, audioBufferList in
            let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
            guard let buffer = buffers.first,
                  let raw = buffer.mData?.assumingMemoryBound(to: Int16.self)
            else { return noErr }

            let wanted = Int(frameCount)
            let got = EmuCore.shared.readAudio(into: raw, frames: wanted)
            if got < wanted {
                // Silence rather than stale audio when the core is behind.
                raw.advanced(by: got * 2).update(repeating: 0, count: (wanted - got) * 2)
            }
            return noErr
        }

        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        source = node

        try? AVAudioSession.sharedInstance().setCategory(.ambient)
        try? AVAudioSession.sharedInstance().setActive(true)
        try? engine.start()
    }

    func stop() {
        engine.stop()
        if let source {
            engine.detach(source)
        }
        source = nil
    }
}

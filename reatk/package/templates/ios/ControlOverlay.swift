import UIKit

/// The on-screen controls, drawn from the layout in `reatk/manifest.json`.
///
/// Rectangles are fractions of the view, so one layout works on every device.
/// Touches are collected here and read once per frame by the view controller,
/// which keeps input on the same clock as emulation.
final class ControlOverlay: UIView {

    struct Input {
        var buttons: UInt16 = 0
        var stylus: CGPoint?
    }

    /// libretro's RETRO_DEVICE_ID_JOYPAD_* bit positions.
    private enum Button: UInt16 {
        case b = 0, y = 1, select = 2, start = 3
        case up = 4, down = 5, left = 6, right = 7
        case a = 8, x = 9, l = 10, r = 11

        static func named(_ id: String) -> Button? {
            switch id.lowercased() {
            case "a": return .a
            case "b": return .b
            case "x": return .x
            case "y": return .y
            case "l": return .l
            case "r": return .r
            case "start": return .start
            case "select": return .select
            default: return nil
            }
        }
    }

    private struct Control {
        let id: String
        let shape: String
        let rect: [Double]
    }

    private let manifest: [String: Any]
    private var controls: [Control] = []
    private var input = Input()

    /// Where the bottom screen is on the device, set by the view controller.
    var screenRect: CGRect = .zero

    /// Frame size the stylus coordinates are expressed in.
    var frameSize = CGSize(width: 256, height: 384)

    var currentInput: Input { input }

    init(manifest: [String: Any]) {
        self.manifest = manifest
        super.init(frame: .zero)
        backgroundColor = .clear
        isMultipleTouchEnabled = true
    }

    required init?(coder: NSCoder) {
        fatalError("not used")
    }

    private func reload() {
        let key = bounds.width > bounds.height ? "landscape" : "portrait"
        let buttons = (manifest["controls"] as? [String: Any])
            .flatMap { $0[key] as? [String: Any] }
            .flatMap { $0["buttons"] as? [[String: Any]] } ?? []

        controls = buttons.compactMap { spec in
            guard let rect = spec["rect"] as? [Double], rect.count == 4 else { return nil }
            return Control(
                id: spec["id"] as? String ?? "",
                shape: spec["shape"] as? String ?? "pill",
                rect: rect
            )
        }
    }

    private func pixels(_ control: Control) -> CGRect {
        CGRect(
            x: control.rect[0] * bounds.width,
            y: control.rect[1] * bounds.height,
            width: control.rect[2] * bounds.width,
            height: control.rect[3] * bounds.height
        )
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        reload()
        setNeedsDisplay()
    }

    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }
        context.setLineWidth(2)

        for control in controls {
            let box = pixels(control)
            let radius = control.shape == "round" ? box.width / 2 : 8
            let path = UIBezierPath(roundedRect: box, cornerRadius: radius)
            UIColor.white.withAlphaComponent(0.12).setFill()
            path.fill()
            UIColor.white.withAlphaComponent(0.35).setStroke()
            path.stroke()

            if control.shape == "dpad" {
                let cross = UIBezierPath()
                cross.move(to: CGPoint(x: box.minX, y: box.midY))
                cross.addLine(to: CGPoint(x: box.maxX, y: box.midY))
                cross.move(to: CGPoint(x: box.midX, y: box.minY))
                cross.addLine(to: CGPoint(x: box.midX, y: box.maxY))
                cross.stroke()
            } else {
                let text = control.id.uppercased() as NSString
                let attributes: [NSAttributedString.Key: Any] = [
                    .foregroundColor: UIColor.white.withAlphaComponent(0.6),
                    .font: UIFont.systemFont(ofSize: 11, weight: .semibold),
                ]
                let size = text.size(withAttributes: attributes)
                text.draw(
                    at: CGPoint(x: box.midX - size.width / 2, y: box.midY - size.height / 2),
                    withAttributes: attributes
                )
            }
        }
    }

    // -- touch -----------------------------------------------------------

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(with: event)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(with: event)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(with: event, ending: touches)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        input = Input()
    }

    private func update(with event: UIEvent?, ending: Set<UITouch> = []) {
        var next = Input()
        for touch in (event?.allTouches ?? []) where !ending.contains(touch) {
            let point = touch.location(in: self)

            if screenRect.contains(point) {
                // The bottom screen is the lower half of the core's frame.
                let x = (point.x - screenRect.minX) / screenRect.width * frameSize.width
                let y = frameSize.height / 2
                    + (point.y - screenRect.minY) / screenRect.height * (frameSize.height / 2)
                next.stylus = CGPoint(x: x, y: y)
                continue
            }

            for control in controls where pixels(control).contains(point) {
                if control.shape == "dpad" {
                    next.buttons |= dpadMask(pixels(control), point)
                } else if let button = Button.named(control.id) {
                    next.buttons |= 1 << button.rawValue
                }
            }
        }
        input = next
    }

    /// Which directions a touch inside the d-pad box means; diagonals included.
    private func dpadMask(_ box: CGRect, _ point: CGPoint) -> UInt16 {
        var mask: UInt16 = 0
        let third = 1.0 / 3.0
        let x = (point.x - box.minX) / box.width
        let y = (point.y - box.minY) / box.height
        if x < third { mask |= 1 << Button.left.rawValue }
        if x > 1 - third { mask |= 1 << Button.right.rawValue }
        if y < third { mask |= 1 << Button.up.rawValue }
        if y > 1 - third { mask |= 1 << Button.down.rawValue }
        return mask
    }
}

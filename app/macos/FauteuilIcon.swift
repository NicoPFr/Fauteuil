import AppKit
import CoreGraphics

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let formats = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
               (256, 1), (256, 2), (512, 1), (512, 2)]
let rgb = CGColorSpace(name: CGColorSpace.sRGB)!
func color(_ hex: Int) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: 1)
}

for (size, scale) in formats {
    let pixels = size * scale
    let ctx = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8,
                        bytesPerRow: 0, space: rgb, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
    let tile = CGPath(roundedRect: CGRect(x: 72, y: 72, width: 880, height: 880),
                      cornerWidth: 205, cornerHeight: 205, transform: nil)
    ctx.addPath(tile)
    ctx.clip()
    let background = CGGradient(colorsSpace: rgb, colors: [color(0x38242B), color(0x181119)] as CFArray,
                                locations: nil)!
    ctx.drawLinearGradient(background, start: CGPoint(x: 200, y: 950), end: CGPoint(x: 780, y: 75),
                           options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])

    func rounded(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, r: CGFloat, fill: Int) {
        ctx.setFillColor(color(fill))
        ctx.addPath(CGPath(roundedRect: CGRect(x: x, y: y, width: w, height: h),
                           cornerWidth: r, cornerHeight: r, transform: nil))
        ctx.fillPath()
    }

    func velvet(_ rect: CGRect, radius: CGFloat, top: Int, bottom: Int) {
        ctx.saveGState()
        ctx.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil))
        ctx.clip()
        let shading = CGGradient(colorsSpace: rgb, colors: [color(top), color(bottom)] as CFArray,
                                 locations: nil)!
        ctx.drawLinearGradient(shading, start: CGPoint(x: rect.midX, y: rect.maxY),
                               end: CGPoint(x: rect.midX, y: rect.minY), options: [])
        ctx.restoreGState()
    }

    ctx.setFillColor(color(0x150D13))
    ctx.fillEllipse(in: CGRect(x: 194, y: 164, width: 636, height: 88))
    for x: CGFloat in [280, 658] {
        rounded(x, 194, 86, 155, r: 22, fill: 0x582332)
        rounded(x + 7, 194, 72, 30, r: 12, fill: 0xB97058)
    }

    velvet(CGRect(x: 274, y: 352, width: 476, height: 466), radius: 138,
           top: 0xF06766, bottom: 0x7B1127)
    velvet(CGRect(x: 302, y: 378, width: 420, height: 407), radius: 111,
           top: 0xE6414B, bottom: 0x94112A)
    rounded(326, 627, 372, 130, r: 58, fill: 0xD63241)
    rounded(362, 727, 300, 15, r: 7, fill: 0xEB555B)

    velvet(CGRect(x: 224, y: 260, width: 576, height: 141), radius: 55,
           top: 0xD63042, bottom: 0x8A132B)
    velvet(CGRect(x: 280, y: 310, width: 464, height: 114), radius: 45,
           top: 0xF25556, bottom: 0xBD2238)
    rounded(315, 401, 394, 12, r: 6, fill: 0xEF6D67)

    for x: CGFloat in [186, 710] {
        velvet(CGRect(x: x, y: 262, width: 128, height: 212), radius: 43,
               top: 0xD93849, bottom: 0xA21B33)
        velvet(CGRect(x: x - 10, y: 431, width: 148, height: 68), radius: 30,
               top: 0xFE6864, bottom: 0xD42D3E)
    }

    let png = NSBitmapImageRep(cgImage: ctx.makeImage()!).representation(using: .png, properties: [:])!
    let suffix = scale == 2 ? "@2x" : ""
    try png.write(to: output.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
}

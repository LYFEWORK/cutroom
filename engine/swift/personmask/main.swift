// personmask: Apple Vision person segmentation for talking-head mattes.
// usage: personmask frame1.png frame2.png ... -> writes frameN.mask.png (8-bit alpha, image size)
import Foundation
import Vision
import CoreImage
import AppKit
let ctx = CIContext(options: nil)
for path in CommandLine.arguments.dropFirst() {
    guard let img = CIImage(contentsOf: URL(fileURLWithPath: path)) else { print("ERR \(path) load"); continue }
    let req = VNGeneratePersonSegmentationRequest()
    req.qualityLevel = .accurate
    req.outputPixelFormat = kCVPixelFormatType_OneComponent8
    let handler = VNImageRequestHandler(ciImage: img, options: [:])
    do { try handler.perform([req]) } catch { print("ERR \(path) \(error)"); continue }
    guard let res = req.results?.first else { print("ERR \(path) noresult"); continue }
    let mask = CIImage(cvPixelBuffer: res.pixelBuffer)
    let sx = img.extent.width / mask.extent.width, sy = img.extent.height / mask.extent.height
    let scaled = mask.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
    let rect = CGRect(x: 0, y: 0, width: img.extent.width, height: img.extent.height)
    guard let cg = ctx.createCGImage(scaled, from: rect) else { print("ERR \(path) cg"); continue }
    let rep = NSBitmapImageRep(cgImage: cg)
    let out = path.replacingOccurrences(of: ".png", with: ".mask.png")
    if let data = rep.representation(using: .png, properties: [:]) { try? data.write(to: URL(fileURLWithPath: out)); print("OK \(out)") }
}

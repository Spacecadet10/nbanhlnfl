import PDFKit
let u = URL(fileURLWithPath: CommandLine.arguments[1])
if let d = PDFDocument(url: u) { print(d.string ?? "") }

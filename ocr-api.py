import ocrmypdf

ocrmypdf.ocr(
    "test-files/arabic/Doc 13.pdf",       # input
    "OCR Benchmarks/Doc 13-api.pdf",      # output
    language="eng+ara",
    deskew=True,
    clean=True,
    clean_final=True,
    force_ocr=True
)

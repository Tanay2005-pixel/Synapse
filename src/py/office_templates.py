"""Blank documents for "Create File".

A 0-byte .docx/.xlsx/.pptx is not a valid document (Office refuses to open
it), so new Office files are created from minimal, valid Office Open XML
packages generated here with the standard library. Plain-text formats are
created empty.

These packages were checked by opening them in Microsoft Word, Excel and
PowerPoint (see tests/verify_office_templates.ps1).
"""

import io
import zipfile

# Formats "Create File" supports.  None = start empty (0 bytes).
TEXT_FORMATS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}
OFFICE_FORMATS = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
CREATABLE = {**TEXT_FORMATS, **OFFICE_FORMATS}

_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _package(parts: dict[str, str]) -> bytes:
    """Zip the parts deterministically (fixed timestamps), so every blank
    document of a type is byte-identical and dedupes in chunk storage."""
    buf = io.BytesIO()
    names = ["[Content_Types].xml"] + [n for n in parts if n != "[Content_Types].xml"]
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:  # [Content_Types].xml first, as Office writes it
            info = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, parts[name])
    return buf.getvalue()


def _rels(*rels: tuple[str, str, str]) -> str:
    body = "".join(
        f'<Relationship Id="{rid}" Type="{_R}/{kind}" Target="{target}"/>'
        for rid, kind, target in rels
    )
    return f'{_XML}<Relationships xmlns="{_PR}">{body}</Relationships>'


def _types(overrides: dict[str, str]) -> str:
    body = "".join(
        f'<Override PartName="{part}" ContentType="{ctype}"/>'
        for part, ctype in overrides.items()
    )
    return (
        f'{_XML}<Types xmlns="{_CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{body}</Types>"
    )


def blank_docx() -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    return _package({
        "[Content_Types].xml": _types({
            "/word/document.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        }),
        "_rels/.rels": _rels(("rId1", "officeDocument", "word/document.xml")),
        "word/document.xml": (
            f'{_XML}<w:document xmlns:w="{w}"><w:body><w:p/>'
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
            'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
            "</w:body></w:document>"
        ),
    })


def blank_xlsx() -> bytes:
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    return _package({
        "[Content_Types].xml": _types({
            "/xl/workbook.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            "/xl/worksheets/sheet1.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            "/xl/styles.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
        }),
        "_rels/.rels": _rels(("rId1", "officeDocument", "xl/workbook.xml")),
        "xl/workbook.xml": (
            f'{_XML}<workbook xmlns="{s}" xmlns:r="{_R}">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": _rels(
            ("rId1", "worksheet", "worksheets/sheet1.xml"),
            ("rId2", "styles", "styles.xml"),
        ),
        "xl/worksheets/sheet1.xml": f'{_XML}<worksheet xmlns="{s}"><sheetData/></worksheet>',
        "xl/styles.xml": (
            f'{_XML}<styleSheet xmlns="{s}">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"
        ),
    })


def _theme() -> str:
    def solid(color: str) -> str:
        return f'<a:solidFill><a:schemeClr val="{color}"/></a:solidFill>'

    colors = {
        "dk1": '<a:sysClr val="windowText" lastClr="000000"/>',
        "lt1": '<a:sysClr val="window" lastClr="FFFFFF"/>',
        "dk2": '<a:srgbClr val="44546A"/>', "lt2": '<a:srgbClr val="E7E6E6"/>',
        "accent1": '<a:srgbClr val="4472C4"/>', "accent2": '<a:srgbClr val="ED7D31"/>',
        "accent3": '<a:srgbClr val="A5A5A5"/>', "accent4": '<a:srgbClr val="FFC000"/>',
        "accent5": '<a:srgbClr val="5B9BD5"/>', "accent6": '<a:srgbClr val="70AD47"/>',
        "hlink": '<a:srgbClr val="0563C1"/>', "folHlink": '<a:srgbClr val="954F72"/>',
    }
    clr = "".join(f"<a:{k}>{v}</a:{k}>" for k, v in colors.items())
    font = '<a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
    line = f'<a:ln w="9525">{solid("phClr")}</a:ln>'
    return (
        f'{_XML}<a:theme xmlns:a="{_A}" name="Office Theme"><a:themeElements>'
        f'<a:clrScheme name="Office">{clr}</a:clrScheme>'
        f'<a:fontScheme name="Office"><a:majorFont>{font}</a:majorFont>'
        f"<a:minorFont>{font}</a:minorFont></a:fontScheme>"
        '<a:fmtScheme name="Office">'
        f'<a:fillStyleLst>{solid("phClr") * 3}</a:fillStyleLst>'
        f"<a:lnStyleLst>{line * 3}</a:lnStyleLst>"
        f'<a:effectStyleLst>{"<a:effectStyle><a:effectLst/></a:effectStyle>" * 3}</a:effectStyleLst>'
        f'<a:bgFillStyleLst>{solid("phClr") * 3}</a:bgFillStyleLst>'
        "</a:fmtScheme></a:themeElements></a:theme>"
    )


def blank_pptx() -> bytes:
    ns = f'xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"'
    empty_tree = (
        '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "</p:spTree></p:cSld>"
    )
    color_map = (
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    )
    pml = "application/vnd.openxmlformats-officedocument.presentationml"
    return _package({
        "[Content_Types].xml": _types({
            "/ppt/presentation.xml": f"{pml}.presentation.main+xml",
            "/ppt/slideMasters/slideMaster1.xml": f"{pml}.slideMaster+xml",
            "/ppt/slideLayouts/slideLayout1.xml": f"{pml}.slideLayout+xml",
            "/ppt/slides/slide1.xml": f"{pml}.slide+xml",
            "/ppt/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml",
        }),
        "_rels/.rels": _rels(("rId1", "officeDocument", "ppt/presentation.xml")),
        "ppt/presentation.xml": (
            f"{_XML}<p:presentation {ns}>"
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
            '<p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": _rels(
            ("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
            ("rId2", "slide", "slides/slide1.xml"),
            ("rId3", "theme", "theme/theme1.xml"),
        ),
        "ppt/slideMasters/slideMaster1.xml": (
            f"{_XML}<p:sldMaster {ns}>{empty_tree}{color_map}"
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            "</p:sldMaster>"
        ),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "theme", "../theme/theme1.xml"),
        ),
        "ppt/slideLayouts/slideLayout1.xml": (
            f'{_XML}<p:sldLayout {ns} type="blank" preserve="1">'
            f'{empty_tree.replace("<p:cSld>", "<p:cSld name=" + chr(34) + "Blank" + chr(34) + ">")}'
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
        ),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels(
            ("rId1", "slideMaster", "../slideMasters/slideMaster1.xml"),
        ),
        "ppt/slides/slide1.xml": (
            f"{_XML}<p:sld {ns}>{empty_tree}"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
        ),
        "ppt/slides/_rels/slide1.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
        ),
        "ppt/theme/theme1.xml": _theme(),
    })


def blank_document(extension: str) -> bytes:
    """Bytes for a new, empty document of the given extension (".docx", ...)."""
    extension = extension.casefold()
    if extension == ".docx":
        return blank_docx()
    if extension == ".xlsx":
        return blank_xlsx()
    if extension == ".pptx":
        return blank_pptx()
    if extension in TEXT_FORMATS:
        return b""
    raise ValueError(f"Cannot create {extension} files")

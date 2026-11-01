"""Безопасная загрузка файла: тип определяется по содержимому, а не по имени или заголовку."""
import io

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000  # защита от «бомб декомпрессии»

MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 3


class LoadError(Exception):
    pass


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def load_pages(data: bytes) -> tuple[str, list[Image.Image]]:
    if not data:
        raise LoadError("Файл пустой")
    if len(data) > MAX_BYTES:
        raise LoadError("Файл больше 10 МБ")
    mime = sniff(data)
    if mime is None:
        raise LoadError("Поддерживаются фото и сканы в JPG и PNG, а также PDF")
    try:
        if mime == "application/pdf":
            return mime, _pdf_pages(data)
        im = Image.open(io.BytesIO(data))
        im.load()
        im = ImageOps.exif_transpose(im)  # фото с телефона часто лежит «на боку» по EXIF
        return mime, [im.convert("RGB")]
    except LoadError:
        raise
    except Image.DecompressionBombError:
        raise LoadError("Изображение слишком большое")
    except Exception:
        raise LoadError("Не удалось открыть файл: он повреждён или это не документ")


def _pdf_pages(data: bytes) -> list[Image.Image]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) == 0:
            raise LoadError("В PDF нет страниц")
        pages = []
        for i in range(min(len(pdf), MAX_PDF_PAGES)):
            page = pdf[i]
            w, h = page.get_size()
            scale = min(300 / 72, 3000 / max(w, h))  # около 300 dpi, но не больше 3000 px
            pages.append(page.render(scale=scale).to_pil().convert("RGB"))
        return pages
    finally:
        pdf.close()

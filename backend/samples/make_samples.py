"""Генератор «чистой» пары демо-документов: ТТН №1001 и путевой лист №1001 с согласованными данными.
ТТН делается похожей на фото с телефона (наклон, шум, синие чернила), чтобы показать предобработку.
Запуск: python make_samples.py (нужен Pillow и шрифты Liberation)."""
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
BOLD = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
INK = (35, 60, 140)
f = lambda s, b=False: ImageFont.truetype(BOLD if b else FONT, s)  # noqa: E731
hand = lambda s: ImageFont.truetype(SANS, s)  # noqa: E731

DRIVER, LIC, PLATE, CAR = "Смирнов А.В.", "77ОА 482913", "К 456 ОР 199", "Scania R450"


def line(d, x0, x1, y):
    d.line([(x0, y), (x1, y)], fill="black", width=2)


def waybill():
    W, H = 1700, 1250
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.text((W // 2, 40), "ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ", font=f(40, True), fill="black", anchor="mt")
    d.text((1270, 120), "ТТН №", font=f(30), fill="black"); d.text((1390, 115), "1001", font=hand(36), fill=INK)
    d.text((60, 120), "Срок доставки груза", font=f(26), fill="black")
    d.text((340, 112), "18 октября 2026 г.", font=hand(32), fill=INK)
    d.text((1150, 185), "К путевому листу №", font=f(28), fill="black"); d.text((1420, 180), "1001", font=hand(34), fill=INK)
    rows = [
        ("Организация", "ООО «Северный склад», г. Тверь, ул. Складская, д. 5", 260),
        ("Заказчик (плательщик)", "ООО «Альфа-Ритейл», г. Москва, ул. Тверская, д. 1", 340),
        ("Автомобиль", CAR, 420),
        ("Государственный номерной знак", PLATE, 500),
        ("Водитель", DRIVER, 580),
        ("Удостоверение №", LIC, 660),
        ("Пункт погрузки", "г. Тверь, ул. Складская, д. 5", 740),
        ("Пункт разгрузки", "г. Москва, ул. Тверская, д. 1", 820),
    ]
    for label, value, y in rows:
        d.text((60, y), label, font=f(28), fill="black")
        x = 60 + d.textlength(label, font=f(28)) + 30
        d.text((x, y - 6), value, font=hand(32), fill=INK)
        line(d, x - 10, W - 60, y + 36)
    d.text((W // 2, 920), "СВЕДЕНИЯ О ГРУЗЕ", font=f(30, True), fill="black", anchor="mt")
    d.rectangle([60, 970, W - 60, 1130], outline="black", width=2)
    d.text((80, 990), "Продукты питания, 20 мест, коробки. Масса брутто, т: 4,2", font=hand(30), fill=INK)
    d.text((80, 1060), "Груз принят с исправной пломбой, тарой и упаковкой", font=f(26), fill="black")
    # «фото с телефона»: серый фон, тень, наклон, шум, лёгкое размытие
    bg = Image.new("RGB", (W + 160, H + 160), (150, 150, 145))
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: 255 - v // 6)
    im = Image.composite(im, Image.new("RGB", (W, H), (200, 200, 195)), shade)
    bg.paste(im, (80, 80))
    bg = bg.rotate(1.8, resample=Image.BICUBIC, fillcolor=(150, 150, 145), expand=False)
    random.seed(7)
    px = bg.load()
    for _ in range(60000):
        x, y = random.randrange(bg.width), random.randrange(bg.height)
        c = px[x, y]; n = random.randint(-28, 28)
        px[x, y] = tuple(max(0, min(255, v + n)) for v in c)
    bg = bg.filter(ImageFilter.GaussianBlur(0.8))
    bg.save("ttn-1001-photo.jpg", quality=80)


def trip_sheet():
    W, H = 1240, 1754
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.text((60, 50), "Форма № 4-П в соответствии с приказом Минтранса России от 28.09.2022 № 390", font=f(22), fill="black")
    d.text((60, 110), "ПУТЕВОЙ ЛИСТ ГРУЗОВОГО АВТОМОБИЛЯ", font=f(38, True), fill="black")
    d.text((900, 110), "№ 1001", font=f(38, True), fill="black")
    d.text((60, 180), "Дата: 18.10.2026", font=f(28), fill="black")
    rows = [("Организация", "ООО «ТрансЛайн», г. Москва, ул. Лесная, д. 7", 260),
            ("Марка автомобиля", CAR, 330), ("Государственный номерной знак", PLATE, 400),
            ("Водитель", DRIVER, 470), ("Удостоверение №", LIC, 540)]
    for label, value, y in rows:
        d.text((60, y), label, font=f(28), fill="black")
        x = 60 + d.textlength(label, font=f(28)) + 30
        d.text((x, y), value, font=f(28), fill="black")
        line(d, x - 10, W - 60, y + 36)
    d.text((60, 640), "прошел предрейсовый медицинский осмотр, к исполнению трудовых обязанностей допущен:", font=f(24), fill="black")
    d.rectangle([60, 685, W - 60, 735], outline="black", width=2)
    d.text((75, 695), "Смирнов А.В.      18.10.2026 06:40      фельдшер      Орлова Е.Н.", font=f(26), fill="black")
    d.text((60, 790), "Предрейсовый контроль технического состояния пройден: 18.10.2026 06:50", font=f(26), fill="black")
    d.text((60, 860), "Показания одометра при выезде, км: 215 340", font=f(26), fill="black")
    d.text((60, 920), "Выезд разрешаю. Механик: Кузнецов П.С.", font=f(26), fill="black")
    d.text((60, 980), "Горючее: ДТ, выдано 300 л", font=f(26), fill="black")
    im.save("trip-sheet-1001-scan.png")


if __name__ == "__main__":
    waybill()
    trip_sheet()

# Odwołania2JSON — obraz uruchomieniowy (CPU, bez GPU, bez zewnętrznych API).
# OCR: Tesseract wbudowany w PyMuPDF + model języka polskiego z ocr/tessdata (w obrazie) — bez pobierania czegokolwiek w czasie działania.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY odwolania2json ./odwolania2json
COPY config ./config
COPY ocr ./ocr
COPY pyproject.toml start.sh ./

RUN mkdir -p input output diagnostics

ENTRYPOINT ["python", "-m", "odwolania2json"]
CMD ["run", "--input", "input", "--output", "output", "--diagnostics", "diagnostics"]

FROM python:3.12-slim

# git is required by the verification agent (clone/checkout/apply/test)
RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./frontend

ENV PYTHONUNBUFFERED=1
ENV DATABASE_URL=sqlite:////app/data/agentic_review.db

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
